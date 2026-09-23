"""Lightweight SQLite persistence for saved designs and their interim (monitoring)
data -- optional, and separate from the pure-computation engine modules.

IMPORTANT -- this only actually PERSISTS if the process it runs in has a
persistent disk. On Streamlit Community Cloud, the filesystem is wiped on
every redeploy and whenever the app sleeps/wakes, so a database written
there survives fine *within* one running session but disappears the next
time the app restarts. It's built to be host-agnostic (point GSD_DB_PATH at
wherever your persistent volume is, e.g. on the EC2+systemd host this
project is ultimately headed for) but nothing here can make Community
Cloud's disk stick around -- that's a hosting decision, not a code one.

Design choices, briefly:
- One file, no server process, stdlib `sqlite3` -- no new dependency.
- Each function opens its own short-lived connection (Streamlit's threading
  model doesn't play well with a long-lived shared connection) and turns on
  WAL mode, which is cheap insurance for overlapping reads/writes even
  though this app's write volume will never stress it.
- `designs` stores DesignInputs fields only, not the computed boundary
  table -- the design math is deterministic, so boundaries are recomputed
  from the stored inputs on load. That way a fixed bug in the engine's math
  is reflected in old saved designs too, instead of them showing whatever
  was computed at save time.
- `interim_looks` is normalized (one row per look x arm) rather than wide
  columns, so it doesn't need a schema change when a design has 1 variant
  vs. 6 -- it just has more rows.
- No migration framework -- at this scale, `CREATE TABLE IF NOT EXISTS`
  plus (if a column is ever added later) a guarded `ALTER TABLE` is enough.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .design import DesignInputs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    baseline REAL NOT NULL,
    std_dev REAL,
    mde REAL NOT NULL,
    mde_is_relative INTEGER NOT NULL,
    alpha REAL NOT NULL,
    power REAL NOT NULL,
    n_looks INTEGER NOT NULL,
    spending_function TEXT NOT NULL,
    futility INTEGER NOT NULL,
    futility_spending_function TEXT NOT NULL,
    sides TEXT NOT NULL,
    n_variants INTEGER NOT NULL,
    weekly_traffic REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS interim_looks (
    design_id INTEGER NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
    look_number INTEGER NOT NULL,
    arm_name TEXT NOT NULL,
    n REAL NOT NULL,
    events REAL,
    mean REAL,
    sd REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (design_id, look_number, arm_name)
);
"""


def db_path() -> Path:
    """Where the SQLite file lives. Override with the GSD_DB_PATH environment
    variable (e.g. pointed at a persistent volume on the EC2 host); defaults
    to a `data/` folder next to the repo root, for local/dev use."""
    override = os.environ.get("GSD_DB_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "data" / "gsd.db"


@contextmanager
def _connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def save_design(name: str, inputs: DesignInputs, weekly_traffic: float = 0.0) -> int:
    """Save a design's inputs (not its computed results -- see module docstring).
    Returns the new design's id."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO designs
               (name, created_at, metric_type, baseline, std_dev, mde, mde_is_relative,
                alpha, power, n_looks, spending_function, futility, futility_spending_function,
                sides, n_variants, weekly_traffic)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name, now, inputs.metric_type, inputs.baseline, inputs.std_dev, inputs.mde,
                int(inputs.mde_is_relative), inputs.alpha, inputs.power, inputs.n_looks,
                inputs.spending_function, int(inputs.futility), inputs.futility_spending_function,
                inputs.sides, inputs.n_variants, weekly_traffic,
            ),
        )
        return cur.lastrowid


def list_designs() -> list[dict]:
    """Most recently saved first. Small summary fields only -- call
    load_design() for the full DesignInputs."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at, metric_type, n_variants, n_looks, weekly_traffic "
            "FROM designs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def load_design(design_id: int) -> tuple[str, DesignInputs, float]:
    """Returns (name, DesignInputs, weekly_traffic). Raises KeyError if not found."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM designs WHERE id = ?", (design_id,)).fetchone()
    if row is None:
        raise KeyError(f"No saved design with id {design_id}")
    inputs = DesignInputs(
        metric_type=row["metric_type"], baseline=row["baseline"], mde=row["mde"],
        mde_is_relative=bool(row["mde_is_relative"]), alpha=row["alpha"], power=row["power"],
        n_looks=row["n_looks"], spending_function=row["spending_function"],
        futility=bool(row["futility"]), futility_spending_function=row["futility_spending_function"],
        std_dev=row["std_dev"], sides=row["sides"], n_variants=row["n_variants"],
    )
    return row["name"], inputs, row["weekly_traffic"]


def delete_design(design_id: int) -> None:
    """Removes a saved design and (via ON DELETE CASCADE) its interim rows."""
    with _connect() as conn:
        conn.execute("DELETE FROM designs WHERE id = ?", (design_id,))


def _arm_columns(arm_name: str) -> tuple[str, str, str, str]:
    prefix = "Control" if arm_name == "Control" else arm_name
    return (f"{prefix} N", f"{prefix} conversions", f"{prefix} mean", f"{prefix} SD")


def save_interim_df(design_id: int, df: pd.DataFrame, metric_type: str, variant_names: list[str]) -> None:
    """Replace all saved interim rows for this design with the current editor
    contents (simplest way to stay correct when rows are added/edited/deleted
    in the data_editor -- a partial upsert would need to also handle deletes)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    for _, row in df.iterrows():
        look = row.get("Look")
        if look is None or pd.isna(look):
            continue
        look_number = int(look)
        for arm_name in ["Control"] + list(variant_names):
            n_col, ev_col, mean_col, sd_col = _arm_columns(arm_name)
            n_val = row.get(n_col)
            if n_val is None or pd.isna(n_val):
                continue
            events = mean = sd = None
            if metric_type == "binary":
                ev = row.get(ev_col)
                events = None if ev is None or pd.isna(ev) else float(ev)
            else:
                mn, sdv = row.get(mean_col), row.get(sd_col)
                mean = None if mn is None or pd.isna(mn) else float(mn)
                sd = None if sdv is None or pd.isna(sdv) else float(sdv)
            records.append((design_id, look_number, arm_name, float(n_val), events, mean, sd, now))

    with _connect() as conn:
        conn.execute("DELETE FROM interim_looks WHERE design_id = ?", (design_id,))
        conn.executemany(
            """INSERT INTO interim_looks (design_id, look_number, arm_name, n, events, mean, sd, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            records,
        )


def load_interim_df(design_id: int, metric_type: str, variant_names: list[str]) -> pd.DataFrame | None:
    """Reconstructs the wide-format editor DataFrame from saved rows, or
    returns None if nothing's been saved yet for this design."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT look_number, arm_name, n, events, mean, sd FROM interim_looks "
            "WHERE design_id = ? ORDER BY look_number",
            (design_id,),
        ).fetchall()
    if not rows:
        return None

    by_look: dict[int, dict[str, dict]] = {}
    for r in rows:
        by_look.setdefault(r["look_number"], {})[r["arm_name"]] = dict(r)
    look_numbers = sorted(by_look)

    data: dict[str, list] = {"Look": look_numbers}
    for arm_name in ["Control"] + list(variant_names):
        n_col, ev_col, mean_col, sd_col = _arm_columns(arm_name)
        data[n_col] = [by_look[k].get(arm_name, {}).get("n", 0.0) for k in look_numbers]
        if metric_type == "binary":
            data[ev_col] = [by_look[k].get(arm_name, {}).get("events") or 0.0 for k in look_numbers]
        else:
            data[mean_col] = [by_look[k].get(arm_name, {}).get("mean") or 0.0 for k in look_numbers]
            data[sd_col] = [by_look[k].get(arm_name, {}).get("sd") or 0.0 for k in look_numbers]
    return pd.DataFrame(data)

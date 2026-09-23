"""Monitoring-stage page: manual interim data entry -> continue/stop read.

Manual entry because 120F doesn't currently have server-to-server credentials
for Adobe Analytics (or Target) -- type in whatever numbers you're already
pulling from your existing reports. When those credentials exist, this page
is where an automated pull would slot in (replacing the data_editor with a
"pull latest from Adobe" button) without changing anything in engine/.

PERSISTENCE IS OPTIONAL, PER-DESIGN, AND HOST-DEPENDENT. If the current
design was saved on the Design page (see engine/store.py), a "Save entries"
button here writes this table to SQLite and reloads it next time -- but only
if the app is running somewhere with a persistent disk (see store.py's
module docstring; Streamlit Community Cloud's disk does NOT survive a
redeploy or a sleep/wake cycle). An unsaved design still works exactly as
before: everything lives only in this browser tab until the app restarts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from app._theme import apply_brand, eyebrow
from engine import store
from engine.design import suggest_monitoring_cadence
from engine.interim import ArmStats, Look, analyze

st.set_page_config(page_title="Monitor a test -- 120F Sequential Testing", layout="wide")
apply_brand()

eyebrow("Step 2")
st.title("Monitor a test")

if "design_result" not in st.session_state:
    st.warning("No design loaded yet. Go to **Design a test** first and compute a design -- "
               "this page needs the boundaries and max sample size from that step.")
    st.stop()

design_result = st.session_state["design_result"]
inputs = design_result.inputs
variant_names = [f"Variant {chr(65 + i)}" for i in range(inputs.n_variants)]  # Variant A, B, C, ...
design_id = st.session_state.get("design_id")

if design_id is not None:
    st.info(
        f"**Entries save against '{st.session_state.get('design_name', '')}'.** Use the **Save "
        f"entries** button below the table. Keep your own record too, as a backup -- this only "
        f"survives app restarts if the app is hosted somewhere with a persistent disk (not yet "
        f"true on Streamlit Community Cloud).",
        icon="💾",
    )
else:
    st.error(
        "**Nothing on this page is saved.** This design hasn't been saved yet -- go back to the "
        "**Design a test** page and give it a name to enable saving here too. Until then, "
        "refreshing, closing the tab, or the app restarting all lose the table below. Keep your "
        "own record of cumulative numbers (e.g. in the Adobe/Target report you're pulling from) "
        "and re-enter them here each time you check in on a test.",
        icon="💾",
    )

st.caption(
    f"Comparing against: {inputs.metric_type} metric, baseline={inputs.baseline}, "
    f"MDE={inputs.mde:+.0%} ({'relative' if inputs.mde_is_relative else 'absolute'}), "
    f"false-positive budget (alpha)={inputs.alpha} ({inputs.sides}-sided), power={inputs.power}, "
    f"{inputs.n_variants} variant(s), max sample size per variant={design_result.n_max_per_arm:,}."
)

weekly_traffic = st.session_state.get("weekly_traffic", 0)
cadence = (suggest_monitoring_cadence(design_result, weekly_traffic_total=weekly_traffic)
           if weekly_traffic and weekly_traffic > 0 else None)
total_days = cadence.projected_weeks_to_max_n * 7 if cadence is not None else None

with st.expander("Considerations before you check in / act on a result"):
    st.markdown(
        "- **Novelty and day-of-week effects.** Crossing the efficacy boundary is a statistically "
        "valid stop, but if a test has only run a few days it may not yet cover a full weekly cycle "
        "(or a novelty bump that's likely to fade). Where practical, let a test cover at least one "
        "full business cycle (usually 1-2 weeks) before treating an early stop as final -- this is a "
        "CRO practice on top of the design, not something the statistics require.\n"
        "- **Roughly equal traffic allocation is assumed.** The information fraction below is "
        "`min(control N, variant N) / max sample size per variant` per comparison -- if traffic has "
        "been split very unevenly across variants (e.g. 90/10, or one variant paused for a while), "
        "the boundaries were calibrated assuming closer to equal allocation, so treat the read with a "
        "bit more caution.\n"
        "- **Check-in cadence.** Aim for roughly the number of looks this design was planned for "
        "(shown above), spread across the test's expected duration, rather than an ad-hoc schedule -- "
        + ("see the suggested cadence on the **Design a test** page." if cadence is None
           else "you entered expected traffic on the Design page, so a suggested cadence is shown there.")
    )

st.markdown("#### Enter cumulative data at each look")
st.caption(
    f"Pre-filled with {inputs.n_looks} rows, one per planned look from the Design page -- add or "
    f"remove rows below if your actual check-ins end up different. Enter CUMULATIVE totals (not just "
    f"this period's numbers) -- e.g. Look 3's row should include everyone counted in Looks 1 and 2 "
    f"as well."
    + ("" if cadence is None else " See the **expected check-in days** reference below the table -- "
       "a planning guide for when the next check-in is due, from the Design page's traffic estimate, "
       "not a requirement.")
)


def _expected_day_lookup(look_value) -> int | None:
    """Projected calendar day for a given 'Look' value, from the Design page's traffic
    estimate. Returns None if there's no cadence yet, or the look number is out of the
    planned range (e.g. an extra look the user added beyond what was originally planned)."""
    if cadence is None or pd.isna(look_value):
        return None
    n_planned = len(design_result.t)
    idx = int(look_value) - 1
    if 0 <= idx < n_planned:
        return round(design_result.t[idx] * total_days)
    return None


def _default_columns():
    # Pre-populate one row per PLANNED look (from the Design page), numbered to match the
    # "Look" column in that page's boundary table -- gives analysts a ready-made row to fill
    # in at each check-in instead of having to add rows one at a time as the test progresses.
    n = inputs.n_looks
    cols = {"Look": list(range(1, n + 1))}
    cols["Control N"] = [0] * n
    if inputs.metric_type == "binary":
        cols["Control conversions"] = [0] * n
    else:
        cols["Control mean"] = [0.0] * n
        cols["Control SD"] = [0.0] * n
    for name in variant_names:
        cols[f"{name} N"] = [0] * n
        if inputs.metric_type == "binary":
            cols[f"{name} conversions"] = [0] * n
        else:
            cols[f"{name} mean"] = [0.0] * n
            cols[f"{name} SD"] = [0.0] * n
    return pd.DataFrame(cols)


def _column_config():
    """Friendlier display labels + hover help for the data editor, WITHOUT renaming the
    underlying column keys the rest of this page reads by name (row["Control N"], etc.) --
    keeps the rename low-risk and independent of the analysis code below."""
    config = {
        "Look": st.column_config.NumberColumn(
            "Look",
            help="Matches the 'Look' numbering on the Design page's boundary table -- rows are "
                 "pre-filled 1 to N for the planned looks. Doesn't have to match exactly if a real "
                 "check-in gets skipped or an extra one is added; it's for your own reference and "
                 "doesn't affect the analysis, which uses the actual sample sizes you enter, not this "
                 "number.",
            format="%d", min_value=1, step=1,
        ),
        "Control N": st.column_config.NumberColumn(
            "Control — sample size (cumulative)",
            help="Total number of users/sessions seen in CONTROL so far, from the start of the test "
                 "up to this check-in (not just this period's count).",
            min_value=0, step=1,
        ),
    }
    if inputs.metric_type == "binary":
        config["Control conversions"] = st.column_config.NumberColumn(
            "Control — conversions (cumulative)",
            help="Total number of CONVERTING users/sessions in control so far, up to this check-in.",
            min_value=0, step=1,
        )
    else:
        config["Control mean"] = st.column_config.NumberColumn(
            "Control — mean value (cumulative)",
            help="Cumulative mean of the metric (e.g. revenue, AOV) for control, up to this check-in.",
        )
        config["Control SD"] = st.column_config.NumberColumn(
            "Control — std. deviation (cumulative)",
            help="Cumulative standard deviation of the metric for control, up to this check-in.",
            min_value=0.0,
        )
    for name in variant_names:
        config[f"{name} N"] = st.column_config.NumberColumn(
            f"{name} — sample size (cumulative)",
            help=f"Total number of users/sessions seen in {name} so far, up to this check-in.",
            min_value=0, step=1,
        )
        if inputs.metric_type == "binary":
            config[f"{name} conversions"] = st.column_config.NumberColumn(
                f"{name} — conversions (cumulative)",
                help=f"Total number of CONVERTING users/sessions in {name} so far, up to this check-in.",
                min_value=0, step=1,
            )
        else:
            config[f"{name} mean"] = st.column_config.NumberColumn(
                f"{name} — mean value (cumulative)",
                help=f"Cumulative mean of the metric for {name}, up to this check-in.",
            )
            config[f"{name} SD"] = st.column_config.NumberColumn(
                f"{name} — std. deviation (cumulative)",
                help=f"Cumulative standard deviation of the metric for {name}, up to this check-in.",
                min_value=0.0,
            )
    return config


default_df = _default_columns()

# First time this design's id shows up in this session, check the database for anything
# saved against it -- but only once, so it doesn't clobber in-progress (unsaved) edits on
# every rerun afterwards.
prior = None
if design_id is not None and st.session_state.get("interim_loaded_for_id") != design_id:
    db_df = store.load_interim_df(design_id, inputs.metric_type, variant_names)
    st.session_state["interim_loaded_for_id"] = design_id
    if db_df is not None:
        prior = db_df
        st.session_state["interim_df"] = prior

if prior is None:
    # If a design change altered the expected columns (metric type or variant count), don't try
    # to reuse a stale table shape.
    prior = st.session_state.get("interim_df")
    if prior is None or set(prior.columns) != set(default_df.columns):
        prior = default_df

# NOTE: deliberately passing the SAME dataframe shape/column order to this widget on every
# rerun (never reordering or injecting a derived column into it, as an earlier version did
# with a computed "Expected day" column) -- data_editor is stateful and keyed, and reshaping
# the value fed into it on every rerun is what caused edits to occasionally vanish when
# clicking from one cell to another (the widget treats a reshaped value as new external data
# and can reset the in-progress edit). The expected-day reference now lives in its own
# read-only display below, decoupled from this editable table.
edited = st.data_editor(prior, num_rows="dynamic", width="stretch",
                         key="interim_editor", column_config=_column_config())
df = edited
st.session_state["interim_df"] = df  # gets stored, analyzed, or saved below.

if cadence is not None:
    ref = df[["Look"]].copy()
    ref["Expected day"] = ref["Look"].map(_expected_day_lookup)
    ref = ref.dropna()
    if not ref.empty:
        ref["Look"] = ref["Look"].astype(int)
        ref["Expected day"] = ref["Expected day"].astype(int)
        with st.expander("Expected check-in days (from the Design page's traffic estimate)"):
            st.dataframe(ref, width="content", hide_index=True)

button_col, save_col = st.columns([1, 1])
with button_col:
    analyze_clicked = st.button("Analyze", type="primary")
with save_col:
    if design_id is not None:
        if st.button("💾 Save entries"):
            store.save_interim_df(design_id, df, inputs.metric_type, variant_names)
            st.success("Saved.")
    else:
        st.caption("Save this design on the **Design a test** page to enable saving entries here.")

if analyze_clicked:
    rows = df.dropna(how="all")
    looks = []
    try:
        for _, row in rows.iterrows():
            if inputs.metric_type == "binary":
                control = ArmStats(n=float(row["Control N"]), events=float(row["Control conversions"]))
            else:
                control = ArmStats(n=float(row["Control N"]), mean=float(row["Control mean"]), sd=float(row["Control SD"]))
            if control.n <= 0:
                continue
            variants = {}
            for name in variant_names:
                if inputs.metric_type == "binary":
                    variants[name] = ArmStats(n=float(row[f"{name} N"]), events=float(row[f"{name} conversions"]))
                else:
                    variants[name] = ArmStats(n=float(row[f"{name} N"]), mean=float(row[f"{name} mean"]),
                                               sd=float(row[f"{name} SD"]))
            looks.append(Look(control=control, variants=variants, label=str(row.get("Look", ""))))
    except (KeyError, ValueError) as e:
        st.error(f"Couldn't read the data table: {e}")
        st.stop()

    looks = [lk for lk in looks if all(v.n > 0 for v in lk.variants.values())]
    if not looks:
        st.warning("Enter at least one look with N > 0 for the control and every variant.")
        st.stop()

    try:
        results = analyze(design_result, looks)
    except Exception as e:
        st.error(f"Couldn't analyze these looks: {e}")
        st.stop()

    st.markdown("### Result")

    DECISION_STYLE = {
        "STOP_EFFICACY": ("success", "✅", "Stop -- efficacy boundary crossed",
                           "beats the efficacy bar at this information fraction"),
        "STOP_SIGNIFICANT_LOSS": ("error", "🔻", "Stop -- significantly worse",
                                   "crossed the lower (two-sided) boundary"),
        "STOP_FUTILITY": ("warning", "🛑", "Stop -- futility boundary crossed",
                           "unlikely to reach significance even at the planned maximum sample size"),
        "CONTINUE": ("info", "➡️", "Continue", "between the boundaries -- keep collecting data"),
    }

    for name, result in results.items():
        style, icon, headline, blurb = DECISION_STYLE[result.decision]
        box = {"success": st.success, "error": st.error, "warning": st.warning, "info": st.info}[style]
        box(f"**{name}: {headline}.** Latest Z = {result.z[-1]:.3f} ({blurb}), "
            f"at information fraction {result.t_obs[-1]:.2f}.", icon=icon)
        st.caption(
            f"{name} naive point estimate (NOT bias-corrected for sequential monitoring -- see README): "
            f"{result.point_estimate:+.4f}  [{result.ci_low:+.4f}, {result.ci_high:+.4f}]"
        )
        if result.decision in ("STOP_EFFICACY", "STOP_SIGNIFICANT_LOSS") and result.t_obs[-1] < 0.5:
            st.caption(
                f"⏳ This stop came early (information fraction {result.t_obs[-1]:.2f}) -- worth "
                f"confirming the test has run long enough to cover a full weekly cycle before acting "
                f"on it, in case novelty or day-of-week effects are inflating the read."
            )

        fig, ax = plt.subplots(figsize=(8, 3.8))
        fig.patch.set_facecolor("#FAF7F2")
        ax.set_facecolor("#FAF7F2")
        ax.plot(result.t_obs, result.efficacy_bounds, color="#D16A0F", marker="o", linewidth=2, label="Efficacy (win)")
        if result.lower_efficacy_bounds is not None:
            ax.plot(result.t_obs, result.lower_efficacy_bounds, color="#C2381E", marker="o", linewidth=2,
                    label="Lower (significant loss)")
        if result.futility_bounds is not None:
            ax.plot(result.t_obs, result.futility_bounds, color="#6C5F54", marker="o", linewidth=2,
                    linestyle="--", label="Futility")
        ax.plot(result.t_obs, result.z, color="#2C6FB5", marker="D", linewidth=2, label=f"Observed Z ({name})")
        ax.axhline(0, color="#C9C0B7", linewidth=1)
        ax.set_xlabel("Observed information fraction")
        ax.set_ylabel("Z statistic")
        ax.set_title(f"{name}: trajectory vs. boundaries", fontsize=12, fontweight="bold", color="#14100D", loc="left")
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        ax.legend(frameon=False, fontsize=8)
        st.pyplot(fig)

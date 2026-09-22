"""
Alpha/beta error-spending functions for group sequential designs.

An error-spending function alpha*(t) allocates the total Type I error (alpha)
across interim looks as a function of the information fraction t in [0, 1],
with alpha*(0) = 0 and alpha*(1) = alpha. The same functional forms are used
for beta-spending (futility), just with `alpha` replaced by `beta`.

References:
  - Lan, K.K.G. and DeMets, D.L. (1983). "Discrete sequential boundaries for
    clinical trials." Biometrika 70(3), 659-663.
  - Kim, K. and DeMets, D.L. (1987). "Design and analysis of group sequential
    tests based on the type I error spending rate function." Biometrika 74(1).
  - Jennison, C. and Turnbull, B.W. (2000). Group Sequential Methods with
    Applications to Clinical Trials.

All functions here are one-sided: they return the CUMULATIVE error spent by
information fraction t, for total budget `total_error` (alpha or beta).
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def obrien_fleming(t: np.ndarray, total_error: float) -> np.ndarray:
    """Lan-DeMets O'Brien-Fleming-type spending function.

    Very conservative early on (almost nothing spent until late), which is
    why it's the standard default for client-facing CRO tests -- it makes an
    early "false" stop very unlikely, at the cost of little extra sample size
    versus a fixed-horizon test (inflation factor is typically ~1.01-1.05).
    """
    t = np.asarray(t, dtype=float)
    out = np.zeros_like(t)
    mask = t > 0
    z = norm.isf(total_error / 2.0)
    out[mask] = 2.0 - 2.0 * norm.cdf(z / np.sqrt(t[mask]))
    return out


def pocock(t: np.ndarray, total_error: float) -> np.ndarray:
    """Lan-DeMets Pocock-type spending function.

    Spends error much more evenly across looks -- lets you stop earlier on
    strong results, but costs more total sample size (inflation factor
    typically ~1.10-1.30) and has less conservative (more "trigger-happy")
    early boundaries than O'Brien-Fleming.
    """
    t = np.asarray(t, dtype=float)
    out = np.zeros_like(t)
    mask = t > 0
    out[mask] = total_error * np.log(1.0 + (np.e - 1.0) * t[mask])
    return out


def kim_demets(t: np.ndarray, total_error: float, rho: float = 3.0) -> np.ndarray:
    """Kim-DeMets power-family spending function: alpha*(t) = alpha * t^rho.

    rho=1 spends error linearly in information (closer to Pocock in
    behaviour); larger rho (e.g. 3) pushes more error to the end, behaving
    similarly to O'Brien-Fleming. This is the general family the AGILE
    method is built on -- rho is the practical "how conservative do you want
    early looks" dial.
    """
    t = np.asarray(t, dtype=float)
    return total_error * np.clip(t, 0.0, 1.0) ** rho


SPENDING_FUNCTIONS = {
    "obrien_fleming": obrien_fleming,
    "pocock": pocock,
    "kim_demets": kim_demets,
}


def spend(name: str, t: np.ndarray, total_error: float, **kwargs) -> np.ndarray:
    """Dispatch to a spending function by name. See SPENDING_FUNCTIONS."""
    if name not in SPENDING_FUNCTIONS:
        raise ValueError(f"Unknown spending function '{name}'. Options: {list(SPENDING_FUNCTIONS)}")
    return SPENDING_FUNCTIONS[name](t, total_error, **kwargs)

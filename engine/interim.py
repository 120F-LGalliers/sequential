"""
Interim (monitoring-stage) analysis: given manually-entered cumulative data
at a look, compute the test statistic and compare it against boundaries
RECOMPUTED for the actual observed information fraction -- not just the
pre-planned, equally-spaced looks from the design stage.

This is the actual practical benefit of the error-spending-function approach
over classical fixed-K boundaries (Pocock/O'Brien-Fleming without a spending
function): the number and timing of looks don't have to match what was
planned at design time. Someone can check a week late, or add an extra look,
and the boundaries adjust automatically -- as long as you always recompute
from the FULL sequence of observed information fractions so far, which is
what `analyze()` below does (it does not try to incrementally reuse
previously-computed boundaries).

Supports multiple variants against a shared control (see design.py's
n_variants/Bonferroni handling) and two-sided tests (a mirrored lower
"declare significantly worse" boundary, alongside non-binding futility).

NOT YET IMPLEMENTED (see README): bias-corrected point estimates/CIs at
stopping (the point estimate and CI below are the NAIVE fixed-horizon
versions, which are known to be slightly optimistic/anti-conservative once
you've been peeking -- fine for a running readout, not yet fine for a final
client-facing effect-size claim at the moment of stopping).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import norm

from . import spending_functions as sf
from .boundaries import efficacy_boundaries, futility_boundaries
from .design import DesignResult


@dataclass
class ArmStats:
    """Cumulative stats for one arm at one look."""
    n: float
    # binary metric:
    events: float | None = None
    # continuous metric:
    mean: float | None = None
    sd: float | None = None


@dataclass
class Look:
    control: ArmStats
    variants: dict[str, ArmStats]   # variant name -> cumulative stats, e.g. {"Variant A": ...}
    label: str | None = None        # e.g. a date, for display only


@dataclass
class InterimAnalysis:
    variant_name: str
    t_obs: np.ndarray            # observed information fraction at each look so far
    z: np.ndarray                # Z-statistic at each look
    efficacy_bounds: np.ndarray        # recomputed upper ("wins") boundary at each look
    lower_efficacy_bounds: np.ndarray | None  # recomputed lower ("loses"), two-sided only
    futility_bounds: np.ndarray | None
    point_estimate: float        # NAIVE (not bias-corrected) effect size at the latest look
    ci_low: float
    ci_high: float
    decision: str                # "CONTINUE" | "STOP_EFFICACY" | "STOP_FUTILITY" | "STOP_SIGNIFICANT_LOSS"


def _z_and_estimate(metric_type: str, control: ArmStats, variant: ArmStats) -> tuple[float, float, float]:
    """Returns (z, point_estimate, se) for the difference variant - control."""
    if metric_type == "binary":
        p1, p2 = control.events / control.n, variant.events / variant.n
        se = np.sqrt(p1 * (1 - p1) / control.n + p2 * (1 - p2) / variant.n)
        diff = p2 - p1
    elif metric_type == "continuous":
        se = np.sqrt((control.sd ** 2) / control.n + (variant.sd ** 2) / variant.n)
        diff = variant.mean - control.mean
    else:
        raise ValueError(f"Unsupported metric_type: {metric_type}")
    z = diff / se if se > 0 else 0.0
    return z, diff, se


def _analyze_one_variant(design_result: DesignResult, variant_name: str,
                          control_stats: list[ArmStats], variant_stats: list[ArmStats],
                          grid_points: int = 300) -> InterimAnalysis:
    inputs = design_result.inputs
    n_max = design_result.n_max_per_arm

    z_list, diff_list, se_list, n_obs_list = [], [], [], []
    for control, variant in zip(control_stats, variant_stats):
        z, diff, se = _z_and_estimate(inputs.metric_type, control, variant)
        z_list.append(z)
        diff_list.append(diff)
        se_list.append(se)
        n_obs_list.append(min(control.n, variant.n))

    t_obs = np.clip(np.array(n_obs_list) / n_max, 1e-6, 1.0)
    if not np.all(np.diff(t_obs) > 0):
        raise ValueError(f"[{variant_name}] Observed information fractions must be strictly increasing across "
                          f"looks (cumulative sample size must grow at every look)")

    alpha_tail = inputs.alpha_tail
    alpha_spend = sf.spend(inputs.spending_function, t_obs, alpha_tail, **inputs.spending_kwargs)
    alpha_spend[-1] = min(alpha_spend[-1], alpha_tail)
    eff = efficacy_boundaries(t_obs, alpha_spend, grid_points=grid_points)

    lower_bounds = None
    if inputs.sides == "two":
        lower_bounds = -eff.bounds.copy()

    fut_bounds = None
    if inputs.futility:
        beta = 1 - inputs.power
        beta_spend = sf.spend(inputs.futility_spending_function, t_obs, beta, **inputs.futility_spending_kwargs)
        beta_spend[-1] = min(beta_spend[-1], beta)
        fut = futility_boundaries(t_obs, design_result.theta1, beta_spend, grid_points=grid_points)
        fut_bounds = fut.bounds
        if t_obs[-1] >= 0.999:
            fut_bounds[-1] = eff.bounds[-1]

    z_arr = np.array(z_list)
    latest_z = z_arr[-1]
    latest_eff = eff.bounds[-1]
    latest_fut = fut_bounds[-1] if fut_bounds is not None else None
    latest_lower = lower_bounds[-1] if lower_bounds is not None else None

    if latest_z >= latest_eff:
        decision = "STOP_EFFICACY"
    elif latest_lower is not None and latest_z <= latest_lower:
        decision = "STOP_SIGNIFICANT_LOSS"
    elif latest_fut is not None and latest_z <= latest_fut:
        decision = "STOP_FUTILITY"
    else:
        decision = "CONTINUE"

    z_crit = norm.isf(alpha_tail)  # naive fixed-horizon CI width, NOT bias-corrected -- see module docstring
    diff, se = diff_list[-1], se_list[-1]
    ci_low, ci_high = diff - z_crit * se, diff + z_crit * se

    return InterimAnalysis(
        variant_name=variant_name,
        t_obs=t_obs,
        z=z_arr,
        efficacy_bounds=eff.bounds,
        lower_efficacy_bounds=lower_bounds,
        futility_bounds=fut_bounds,
        point_estimate=diff,
        ci_low=ci_low,
        ci_high=ci_high,
        decision=decision,
    )


def analyze(design_result: DesignResult, looks: list[Look], grid_points: int = 300) -> dict[str, InterimAnalysis]:
    """Analyze every variant present in `looks` against the shared control.
    Every look must name the same set of variants (in any order)."""
    if not looks:
        raise ValueError("Need at least one look")

    variant_names = list(looks[0].variants.keys())
    for look in looks:
        if set(look.variants.keys()) != set(variant_names):
            raise ValueError("Every look must report the same set of variant names")

    if design_result.inputs.n_variants != len(variant_names):
        raise ValueError(
            f"Design was built for n_variants={design_result.inputs.n_variants}, "
            f"but {len(variant_names)} variant(s) were provided in the data ({variant_names}). "
            f"These must match -- the Bonferroni adjustment depends on the number of variants."
        )

    control_stats = [look.control for look in looks]
    results = {}
    for name in variant_names:
        variant_stats = [look.variants[name] for look in looks]
        results[name] = _analyze_one_variant(design_result, name, control_stats, variant_stats,
                                              grid_points=grid_points)
    return results

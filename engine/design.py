"""
Design-stage calculator: turns business inputs (baseline rate/mean, MDE,
alpha, power, number of looks) into a group sequential test design --
boundary table, maximum sample size, and expected sample size under H0/H1.

Supports binary (conversion rate) and continuous (mean-based, e.g.
revenue/AOV) metrics. Count/ratio metrics can reuse the continuous path if
they're modeled as approximately normal per-user values (e.g. items per
order) -- flagged as a TODO to confirm this is an adequate approximation for
the specific metrics 120F wants to support (see project doc open items).

Sides and multiple variants
----------------------------
- `sides="one"` (default): tests "does the variant beat control" only.
  `sides="two"`: also lets you declare a variant significantly WORSE than
  control. Implemented by mirroring: the upper (win) boundary is solved as
  before but with HALF the per-comparison alpha in each tail, and the lower
  (loss) boundary is its exact negative -- valid because the process is
  symmetric about 0 under the null (theta=0), so a one-sided calculation
  with alpha/2 for the upper tail gives, by symmetry, exactly the boundary
  a one-sided calculation for the lower tail with alpha/2 would give.

- `n_variants` > 1: more than one variant compared against a shared control.
  Controlled via a BONFERRONI correction (alpha divided by n_variants
  before anything else happens) -- simple, always valid, but conservative.
  It does NOT model the correlation between comparisons that share a
  control arm (a Dunnett-style joint calculation would be less
  conservative but is a meaningfully bigger lift) -- flagged as a possible
  future improvement if Bonferroni's conservatism becomes a real cost.
  All comparisons share one design (same alpha/power/boundaries); only the
  per-variant Z-statistic differs at the monitoring stage (see interim.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from . import spending_functions as sf
from .boundaries import BoundaryResult, crossing_probability, efficacy_boundaries, futility_boundaries


@dataclass
class DesignInputs:
    metric_type: str            # "binary" | "continuous"
    baseline: float             # baseline conversion rate (binary) or baseline mean (continuous)
    mde: float                  # minimum detectable effect
    mde_is_relative: bool = True  # if True, mde is a relative lift (e.g. 0.10 = +10%); else absolute
    alpha: float = 0.05         # FAMILY-WISE Type I error budget (before any Bonferroni/sides split)
    power: float = 0.80         # target power (1 - beta), for detecting the hypothesized-direction effect
    n_looks: int = 5
    spending_function: str = "obrien_fleming"
    spending_kwargs: dict = field(default_factory=dict)
    futility: bool = True
    futility_spending_function: str = "obrien_fleming"
    futility_spending_kwargs: dict = field(default_factory=dict)
    std_dev: float | None = None  # required for continuous metrics: per-user standard deviation
    sides: str = "one"          # "one" | "two"
    n_variants: int = 1         # number of variants vs. the shared control (Bonferroni-adjusted)

    @property
    def alpha_per_comparison(self) -> float:
        """Family-wise alpha divided across variants (Bonferroni)."""
        return self.alpha / self.n_variants

    @property
    def alpha_tail(self) -> float:
        """The alpha actually spent in ONE tail's boundary calculation --
        halved again if two-sided."""
        return self.alpha_per_comparison / 2 if self.sides == "two" else self.alpha_per_comparison


@dataclass
class DesignResult:
    inputs: DesignInputs
    t: np.ndarray
    efficacy: BoundaryResult          # upper ("variant wins") boundary
    lower_efficacy: BoundaryResult | None  # lower ("variant loses"), only set if sides="two"
    futility: BoundaryResult | None
    n_fixed_per_arm: int
    n_max_per_arm: int
    inflation_factor: float
    expected_n_under_h0: float
    expected_n_under_h1: float
    theta1: float  # standardized effect size (drift) the design is powered for


def _fixed_sample_size(inputs: DesignInputs) -> tuple[int, float]:
    """Standard (non-sequential) required sample size per arm, and the
    standardized effect size theta1 = the fixed-sample non-centrality
    z_alpha_tail + z_beta, at the ALREADY Bonferroni/sides-adjusted tail
    alpha (inputs.alpha_tail)."""
    z_alpha = norm.isf(inputs.alpha_tail)
    z_beta = norm.isf(1 - inputs.power)
    theta1_fixed = z_alpha + z_beta  # required non-centrality, fixed-sample design

    if inputs.metric_type == "binary":
        p1 = inputs.baseline
        p2 = p1 * (1 + inputs.mde) if inputs.mde_is_relative else p1 + inputs.mde
        if not (0 < p2 < 1):
            raise ValueError(f"Variant rate implied by MDE is out of (0,1): {p2}")
        pooled_var = p1 * (1 - p1) + p2 * (1 - p2)
        delta = abs(p2 - p1)
        n = pooled_var * (theta1_fixed ** 2) / (delta ** 2)
    elif inputs.metric_type == "continuous":
        if inputs.std_dev is None:
            raise ValueError("std_dev is required for continuous metrics")
        delta = inputs.baseline * inputs.mde if inputs.mde_is_relative else inputs.mde
        n = 2 * (inputs.std_dev ** 2) * (theta1_fixed ** 2) / (delta ** 2)
    else:
        raise ValueError(f"Unsupported metric_type: {inputs.metric_type}")

    return int(np.ceil(n)), theta1_fixed


def design(inputs: DesignInputs, grid_points: int = 300) -> DesignResult:
    if inputs.sides not in ("one", "two"):
        raise ValueError(f"sides must be 'one' or 'two', got {inputs.sides!r}")
    if inputs.n_variants < 1:
        raise ValueError("n_variants must be >= 1")

    t = np.linspace(1.0 / inputs.n_looks, 1.0, inputs.n_looks)

    n_fixed, theta1_fixed = _fixed_sample_size(inputs)

    alpha_tail = inputs.alpha_tail
    alpha_spend = sf.spend(inputs.spending_function, t, alpha_tail, **inputs.spending_kwargs)
    alpha_spend[-1] = alpha_tail  # ensure exact final spend (avoids float rounding leaving a gap)
    efficacy = efficacy_boundaries(t, alpha_spend, grid_points=grid_points)

    lower_efficacy = None
    if inputs.sides == "two":
        # Exact mirror by symmetry under H0 -- see module docstring.
        lower_efficacy = BoundaryResult(t=t, bounds=-efficacy.bounds.copy(),
                                         incremental_prob=efficacy.incremental_prob.copy(), theta=0.0)

    # Solve for the drift theta1_seq such that the SEQUENTIAL design (using
    # the upper/"wins" boundary) achieves the target power for detecting the
    # hypothesized-direction effect. This determines the sample-size
    # inflation factor vs the fixed-sample design, same convention whether
    # one- or two-sided (standard practice: two-sided sample-size formulas
    # still power toward ONE assumed true-effect direction).
    def achieved_power(theta1_candidate: float) -> float:
        incr = crossing_probability(t, efficacy.bounds, theta1_candidate, direction="upper", grid_points=grid_points)
        return float(np.sum(incr))

    theta1_seq = brentq(lambda th: achieved_power(th) - inputs.power, 1e-6, 20.0, xtol=1e-8, rtol=1e-10)
    inflation_factor = (theta1_seq / theta1_fixed) ** 2
    n_max = int(np.ceil(n_fixed * inflation_factor))

    futility_res = None
    if inputs.futility:
        beta = 1 - inputs.power
        beta_spend = sf.spend(inputs.futility_spending_function, t, beta, **inputs.futility_spending_kwargs)
        beta_spend[-1] = beta
        futility_res = futility_boundaries(t, theta1_seq, beta_spend, grid_points=grid_points)
        # Standard convention: force convergence at the final look.
        futility_res.bounds[-1] = efficacy.bounds[-1]

    # Expected sample size under H0 and H1: E[N] = n_max * sum_k t_k * P(stop exactly at look k).
    # NOTE: when more than one stopping rule is active (efficacy + futility,
    # or efficacy + mirrored lower-efficacy for two-sided), each rule's
    # crossing probability is computed treating the OTHER rules as absent --
    # summing them is a standard, small APPROXIMATION for this summary
    # figure (it can slightly overstate expected N), not an exact joint
    # calculation. It does not affect the boundaries themselves, which are
    # each exactly calibrated to their own target error -- see validate.py.
    def expected_info_fraction(theta_val: float) -> float:
        eff_incr = crossing_probability(t, efficacy.bounds, theta_val, direction="upper", grid_points=grid_points)
        components = [eff_incr]
        if futility_res is not None:
            components.append(crossing_probability(t, futility_res.bounds, theta_val, direction="lower",
                                                     grid_points=grid_points))
        if lower_efficacy is not None:
            components.append(crossing_probability(t, lower_efficacy.bounds, theta_val, direction="lower",
                                                     grid_points=grid_points))
        stop_prob = np.sum(components, axis=0)
        stop_prob[-1] = 1.0 - np.sum(stop_prob[:-1])  # remaining mass stops at final look by design
        return float(np.sum(t * stop_prob))

    expected_n_h0 = n_max * expected_info_fraction(0.0)
    expected_n_h1 = n_max * expected_info_fraction(theta1_seq)

    return DesignResult(
        inputs=inputs,
        t=t,
        efficacy=efficacy,
        lower_efficacy=lower_efficacy,
        futility=futility_res,
        n_fixed_per_arm=n_fixed,
        n_max_per_arm=n_max,
        inflation_factor=inflation_factor,
        expected_n_under_h0=expected_n_h0,
        expected_n_under_h1=expected_n_h1,
        theta1=theta1_seq,
    )


@dataclass
class MonitoringCadence:
    weekly_traffic_per_arm: float
    projected_weeks_to_max_n: float
    suggested_interval_days: float
    suggested_interval_label: str
    first_check_after_days: float
    first_check_info_fraction: float


def suggest_monitoring_cadence(result: DesignResult, weekly_traffic_total: float,
                                min_info_fraction_first_look: float = 0.10) -> MonitoringCadence:
    """Translate a design's max sample size + planned number of looks into a
    suggested CALENDAR cadence, given the test's expected total weekly
    traffic (split evenly across control + all variants -- assumes roughly
    equal allocation, same assumption interim.py's info-fraction calc
    makes).

    This is a planning aid, not a statistical requirement: interim.py
    recomputes boundaries at whatever information fraction is actually
    observed at each look, so drifting from this schedule doesn't break
    error control. It exists to answer the practical question "how often
    should someone actually go and check the data", which genuinely does
    depend on how fast a given test is accruing traffic -- there's no
    single daily/weekly answer that fits both a high-traffic checkout flow
    test and a low-traffic B2B lead-gen form test.

    Two things worth knowing about the number this returns:
    - It targets roughly `n_looks` check-ins spread evenly across the
      projected test duration -- checking much MORE often than planned
      doesn't inflate error, but it does erode the sample-size efficiency
      the design was calibrated for (more chances to stop early than
      assumed) and invites eyeballing trajectories between formal looks.
    - `first_check_after_days` suggests skipping the very first sliver of
      the test: with O'Brien-Fleming-style spending, a look before roughly
      10% information is so conservative it essentially cannot stop for
      efficacy or futility, so an earlier check is mostly noise and
      wasted time.
    """
    if weekly_traffic_total <= 0:
        raise ValueError("weekly_traffic_total must be positive")

    n_arms = result.inputs.n_variants + 1  # + 1 for the shared control
    weekly_traffic_per_arm = weekly_traffic_total / n_arms
    projected_weeks = result.n_max_per_arm / weekly_traffic_per_arm
    total_days = projected_weeks * 7

    suggested_interval_days = total_days / result.inputs.n_looks
    first_check_after_days = total_days * min_info_fraction_first_look

    if suggested_interval_days < 1.5:
        label = "daily"
    elif suggested_interval_days < 4:
        label = "every 2-3 days"
    elif suggested_interval_days < 10:
        label = "weekly"
    elif suggested_interval_days < 20:
        label = "every 2 weeks"
    else:
        label = "monthly"

    return MonitoringCadence(
        weekly_traffic_per_arm=weekly_traffic_per_arm,
        projected_weeks_to_max_n=projected_weeks,
        suggested_interval_days=suggested_interval_days,
        suggested_interval_label=label,
        first_check_after_days=first_check_after_days,
        first_check_info_fraction=min_info_fraction_first_look,
    )


def summarize(result: DesignResult) -> str:
    inp = result.inputs
    lines = [
        f"Metric: {inp.metric_type}, baseline={inp.baseline}, "
        f"MDE={inp.mde} ({'relative' if inp.mde_is_relative else 'absolute'})",
        f"False-positive budget (alpha)={inp.alpha} ({inp.sides}-sided), power={inp.power}, "
        f"looks={inp.n_looks}, spending={inp.spending_function}, variants={inp.n_variants}",
    ]
    if inp.n_variants > 1 or inp.sides == "two":
        lines.append(
            f"  -> per-comparison alpha (Bonferroni / {inp.n_variants} variant(s)) = {inp.alpha_per_comparison:.5f}; "
            f"per-tail alpha used in boundary calc = {inp.alpha_tail:.5f}"
        )
    lines += [
        f"Sample size per variant -- standard test (no interim looks): {result.n_fixed_per_arm}",
        f"Sample size per variant -- sequential max (plan capacity for this): {result.n_max_per_arm}  "
        f"(+{100 * (result.inflation_factor - 1):.1f}% vs standard test)",
        f"Sample size per variant -- typical, no real effect: {result.expected_n_under_h0:.0f}  "
        f"({100 * result.expected_n_under_h0 / result.n_max_per_arm:.0f}% of sequential max)",
        f"Sample size per variant -- typical, real effect: {result.expected_n_under_h1:.0f}  "
        f"({100 * result.expected_n_under_h1 / result.n_max_per_arm:.0f}% of sequential max)",
        "",
        f"{'Look':>4} {'% of max':>10} {'N/variant':>10} {'Lower (loss)':>13} {'Futility Z':>11} {'Efficacy Z':>11}",
    ]
    for k in range(inp.n_looks):
        fut_z = f"{result.futility.bounds[k]:.3f}" if result.futility is not None else "n/a"
        lower_z = f"{result.lower_efficacy.bounds[k]:.3f}" if result.lower_efficacy is not None else "n/a"
        lines.append(
            f"{k + 1:>4} {100 * result.t[k]:>9.0f}% {int(round(result.t[k] * result.n_max_per_arm)):>10} "
            f"{lower_z:>13} {fut_z:>11} {result.efficacy.bounds[k]:>11.3f}"
        )
    return "\n".join(lines)

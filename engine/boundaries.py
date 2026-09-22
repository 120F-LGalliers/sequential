"""
Group sequential boundary computation via recursive numerical integration.

This implements the standard "canonical joint distribution" method used by
reference software such as R's gsDesign/rpact (Armitage-McPherson-Rowe /
Jennison & Turnbull recursive integration): the sequence of standardized
statistics Z_1..Z_K at information fractions t_1<...<t_K=1 is represented on
the "score scale" S_k = Z_k*sqrt(t_k), a Brownian-motion-with-drift process
with independent increments:

    S_k - S_(k-1) ~ Normal(theta * (t_k - t_(k-1)), t_k - t_(k-1))

where theta is the standardized drift per unit information (theta = 0 under
the null; theta = the design effect's non-centrality at t=1 under the
alternative). This holds asymptotically for the standard two-sample Z-tests
used for conversion-rate and mean-difference metrics.

Scope for this v1: ONE-SIDED tests only, with NON-BINDING futility (the
efficacy boundary is computed ignoring the futility rule, and vice versa --
the standard, simpler, and most common convention; see README).

WARNING: this is a from-scratch numerical implementation. It is validated in
validate.py against (a) an independent Monte Carlo simulation and (b) the
classic closed-form O'Brien-Fleming boundaries, but it has NOT been
cross-checked against a second reference implementation (e.g. R's rpact /
gsDesign) because this sandbox cannot reach CRAN. Do not use this for a real
client-facing stop/go decision until that cross-check has been done -- see
the "Validation" open item in the project doc.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

DEFAULT_GRID_POINTS = 300  # per-step quadrature grid size; even number required for Simpson's rule
GRID_HALF_WIDTH_SD = 8.0  # grid spans +/- this many standard deviations


def _simpson_grid(lo: float, hi: float, n: int = DEFAULT_GRID_POINTS):
    """Composite Simpson's rule nodes and weights on [lo, hi], n even."""
    if n % 2 != 0:
        n += 1
    x = np.linspace(lo, hi, n + 1)
    h = (hi - lo) / n
    w = np.ones(n + 1)
    w[1:-1:2] = 4.0
    w[2:-1:2] = 2.0
    w *= h / 3.0
    return x, w


@dataclass
class BoundaryResult:
    t: np.ndarray                 # information fractions
    bounds: np.ndarray            # Z-scale boundary at each look
    incremental_prob: np.ndarray  # prob of crossing exactly at look k (under the theta used to solve), should sum to total_error
    theta: float                  # drift used to calibrate these bounds


def _solve_one_sided(t: np.ndarray, theta: float, increments: np.ndarray, direction: str,
                      grid_points: int = DEFAULT_GRID_POINTS) -> BoundaryResult:
    """Recursively solve for one-sided boundaries crossing which spends
    `increments[k]` of probability (under drift `theta`) at look k.

    direction='upper': boundary is an efficacy-style ceiling (stop if S_k/sqrt(t_k) >= bound).
    direction='lower': boundary is a futility-style floor   (stop if S_k/sqrt(t_k) <= bound).
    """
    K = len(t)
    bounds = np.empty(K)
    incr_prob = np.empty(K)

    # "Step 0" state: the process starts at S_0 = 0 with certainty (a degenerate
    # distribution), so the first real step (k=0) is just S_1 ~ Normal(theta*t_1, t_1).
    u_prev = np.array([0.0])
    w_prev = np.array([1.0])
    h_prev = np.array([1.0])
    t_prev = 0.0

    for k in range(K):
        dt = t[k] - t_prev
        sd = np.sqrt(dt)

        def crossing_prob(bound_k: float) -> float:
            s_boundary = bound_k * np.sqrt(t[k])
            mean = u_prev + theta * dt
            if direction == "upper":
                tail = norm.sf((s_boundary - mean) / sd)
            else:
                tail = norm.cdf((s_boundary - mean) / sd)
            return float(np.sum(w_prev * h_prev * tail))

            # (h_prev already incorporates survival to look k-1; w_prev are the
            # quadrature weights for u_prev, the grid representing S_(k-1).)

        target = increments[k]
        # crossing_prob is monotone in bound_k (decreasing for 'upper', increasing for 'lower').
        lo_search, hi_search = -20.0, 20.0
        f_lo, f_hi = crossing_prob(lo_search), crossing_prob(hi_search)
        if direction == "upper":
            f = lambda b: crossing_prob(b) - target
        else:
            f = lambda b: crossing_prob(b) - target
        # Guard against a target outside the achievable range (can happen with
        # pathological spending near t->0); clip rather than raise.
        if direction == "upper" and (f_lo - target) * (f_hi - target) > 0:
            bound_k = hi_search if crossing_prob(lo_search) < target else lo_search
        elif direction == "lower" and (f_lo - target) * (f_hi - target) > 0:
            bound_k = lo_search if crossing_prob(hi_search) > target else hi_search
        else:
            bound_k = brentq(f, lo_search, hi_search, xtol=1e-10, rtol=1e-12, maxiter=200)

        bounds[k] = bound_k
        incr_prob[k] = crossing_prob(bound_k)

        if k < K - 1:
            # Build the continuation-region grid for S_k and evaluate h_k on it,
            # to seed the next step's convolution.
            s_boundary = bound_k * np.sqrt(t[k])
            if direction == "upper":
                grid_lo, grid_hi = -GRID_HALF_WIDTH_SD * np.sqrt(t[k]), s_boundary
            else:
                grid_lo, grid_hi = s_boundary, GRID_HALF_WIDTH_SD * np.sqrt(t[k])
            grid_k, weights_k = _simpson_grid(grid_lo, grid_hi, grid_points)

            diff = grid_k[:, None] - u_prev[None, :]
            phi_mat = norm.pdf(diff, loc=theta * dt, scale=sd)
            h_k = phi_mat @ (w_prev * h_prev)

            u_prev, w_prev, h_prev = grid_k, weights_k, h_k
            t_prev = t[k]

    return BoundaryResult(t=t, bounds=bounds, incremental_prob=incr_prob, theta=theta)


def efficacy_boundaries(t: np.ndarray, alpha_spend: np.ndarray, grid_points: int = DEFAULT_GRID_POINTS) -> BoundaryResult:
    """Efficacy (upper, one-sided) boundaries under H0 (theta=0), calibrated
    so cumulative crossing probability at each look matches alpha_spend
    (the CUMULATIVE alpha-spending function values -- this function takes
    care of differencing them into per-look increments)."""
    incr = np.diff(np.concatenate(([0.0], alpha_spend)))
    return _solve_one_sided(t, theta=0.0, increments=incr, direction="upper", grid_points=grid_points)


def futility_boundaries(t: np.ndarray, theta1: float, beta_spend: np.ndarray,
                         grid_points: int = DEFAULT_GRID_POINTS) -> BoundaryResult:
    """Futility (lower, one-sided) boundaries under H1 (theta=theta1),
    calibrated so cumulative crossing probability matches beta_spend.
    Non-binding: computed independently of the efficacy boundary."""
    incr = np.diff(np.concatenate(([0.0], beta_spend)))
    return _solve_one_sided(t, theta=theta1, increments=incr, direction="lower", grid_points=grid_points)


def crossing_probability(t: np.ndarray, bounds: np.ndarray, theta: float, direction: str = "upper",
                          grid_points: int = DEFAULT_GRID_POINTS) -> np.ndarray:
    """Given FIXED boundaries, compute the incremental probability of
    crossing them at each look under drift `theta`. Used to (a) check that
    solved boundaries reproduce their target spend, and (b) compute the
    achieved power of a design (crossing_probability(..., theta=theta1)
    summed over all looks)."""
    K = len(t)
    incr_prob = np.empty(K)
    u_prev = np.array([0.0])
    w_prev = np.array([1.0])
    h_prev = np.array([1.0])
    t_prev = 0.0
    for k in range(K):
        dt = t[k] - t_prev
        sd = np.sqrt(dt)
        s_boundary = bounds[k] * np.sqrt(t[k])
        mean = u_prev + theta * dt
        tail = norm.sf((s_boundary - mean) / sd) if direction == "upper" else norm.cdf((s_boundary - mean) / sd)
        incr_prob[k] = float(np.sum(w_prev * h_prev * tail))

        if k < K - 1:
            if direction == "upper":
                grid_lo, grid_hi = -GRID_HALF_WIDTH_SD * np.sqrt(t[k]), s_boundary
            else:
                grid_lo, grid_hi = s_boundary, GRID_HALF_WIDTH_SD * np.sqrt(t[k])
            grid_k, weights_k = _simpson_grid(grid_lo, grid_hi, grid_points)
            diff = grid_k[:, None] - u_prev[None, :]
            phi_mat = norm.pdf(diff, loc=theta * dt, scale=sd)
            h_k = phi_mat @ (w_prev * h_prev)
            u_prev, w_prev, h_prev = grid_k, weights_k, h_k
            t_prev = t[k]
    return incr_prob

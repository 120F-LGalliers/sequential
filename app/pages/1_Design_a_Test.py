"""Design-stage calculator page."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, so `import engine`/`import app` work

import matplotlib.pyplot as plt
import streamlit as st

from app._theme import apply_brand, eyebrow
from engine.design import DesignInputs, design, suggest_monitoring_cadence, summarize
from engine.spending_functions import SPENDING_FUNCTIONS

st.set_page_config(page_title="Design a test -- 120F Sequential Testing", layout="wide")
apply_brand()

eyebrow("Step 1")
st.title("Design a test")

with st.form("design_form"):
    c1, c2 = st.columns(2)
    with c1:
        metric_type = st.radio("Metric type", ["binary", "continuous"], horizontal=True,
                                help="Binary = conversion rate. Continuous = a per-user mean, e.g. revenue or AOV.")
        baseline = st.number_input(
            "Baseline" + (" conversion rate (e.g. 0.10 for 10%)" if metric_type == "binary" else " mean"),
            min_value=0.0001, value=0.10 if metric_type == "binary" else 50.0, format="%.4f",
            help="Pull this from a recent, representative pre-test window (4-6 weeks is usually enough "
                 "to smooth out day-of-week noise) rather than a single day or a period with a promo, "
                 "holiday, or outage in it -- the whole design (sample size, boundaries) is calibrated "
                 "off this number, so a skewed baseline skews everything downstream.")
        std_dev = None
        if metric_type == "continuous":
            std_dev = st.number_input(
                "Per-user standard deviation", min_value=0.0001, value=30.0,
                help="Also pull this from historical data over the same window as the baseline, not a "
                     "guess. High-variance metrics (e.g. revenue with a few very large orders) inflate "
                     "required sample size a lot -- if a handful of outliers dominate the standard "
                     "deviation, consider capping/winsorizing the metric or using a more robust one "
                     "(e.g. conversion-to-any-purchase) before designing off it.")
        mde_is_relative = st.radio("MDE type", ["relative", "absolute"], horizontal=True) == "relative"
        mde = st.number_input(
            "Minimum detectable effect" + (" (e.g. 0.10 = +10% relative)" if mde_is_relative else " (absolute units)"),
            value=0.10, format="%.4f",
            help="Pick the smallest effect that would actually change a decision if you found it -- "
                 "not the smallest effect you can imagine. Smaller MDE = quadratically bigger required "
                 "sample size, so an unrealistically small MDE can make a test take months to reach a "
                 "read. If traffic is limited, it's usually better to widen the MDE to something still "
                 "commercially meaningful than to run an underpowered test.")
        sides = st.radio(
            "Sides", ["one", "two"], horizontal=True,
            format_func=lambda s: "One-sided (does the variant beat control?)" if s == "one"
            else "Two-sided (also flag a significant loss)",
            help="One-sided is the standard CRO framing. Two-sided also gives you a boundary for "
                 "declaring a variant significantly WORSE than control, at the cost of a slightly "
                 "bigger required sample size for the same confidence on the 'beats' question.",
        )
        n_variants = st.number_input(
            "Number of variants vs. control", min_value=1, max_value=6, value=1, step=1,
            help="More than 1 variant splits your alpha budget across comparisons (Bonferroni "
                 "correction) so the overall false-positive rate across all of them stays at your "
                 "chosen alpha -- each comparison gets a stricter effective alpha, and the required "
                 "sample size per arm goes up accordingly.",
        )
    with c2:
        alpha = st.number_input("Family-wise alpha (overall false-positive budget)", min_value=0.001,
                                 max_value=0.5, value=0.05,
                                 help="With more than one variant, this is split across all of them "
                                      "(Bonferroni) -- it's the TOTAL chance of any false positive "
                                      "across the whole test, not per comparison. 0.05 is the standard "
                                      "default; consider tightening it (e.g. 0.01-0.02) for a decision "
                                      "that's expensive or hard to reverse if wrong, such as a "
                                      "site-wide rollout with real engineering cost.")
        power = st.number_input("Target power (1 - beta)", min_value=0.5, max_value=0.999, value=0.80,
                                 help="80% is the standard default -- a 1-in-5 chance of missing a real "
                                      "effect of exactly this MDE. Consider 90% when the cost of missing "
                                      "a real winner is high (it raises required sample size further, on "
                                      "top of the sequential design's own inflation).")
        n_looks = st.slider("Planned number of interim looks", min_value=2, max_value=12, value=5,
                             help="With O'Brien-Fleming spending, adding more looks costs very little "
                                  "extra sample size (the early boundaries are so conservative they're "
                                  "barely used) but gives more chances to stop early -- so there's little "
                                  "downside to planning more looks than you think you'll need. 4-8 is a "
                                  "reasonable range for most tests; this also sets the target cadence "
                                  "below once you enter expected traffic.")
        spending_function = st.selectbox("Efficacy spending function", list(SPENDING_FUNCTIONS.keys()),
                                          index=list(SPENDING_FUNCTIONS.keys()).index("obrien_fleming"),
                                          help="O'Brien-Fleming (recommended default): conservative early on, "
                                               "minimal sample-size cost. Pocock: spends error more evenly, "
                                               "lets you stop earlier on strong results, costs more sample size.")
        futility = st.checkbox(
            "Include a (non-binding) futility stopping rule", value=True,
            help="Recommended for most tests -- lets you stop early and free up traffic for the next "
                 "test when a variant is clearly not going to reach significance, at the cost of "
                 "slightly lower ACHIEVED power/alpha than the nominal targets if the stop is always "
                 "followed (see the README's 'real finding' section). Consider turning it off only if "
                 "you have a separate reason to run every test to its full planned sample regardless "
                 "(e.g. also collecting secondary/qualitative data across the whole period).")
        futility_spending_function = spending_function
        if futility:
            futility_spending_function = st.selectbox(
                "Futility spending function", list(SPENDING_FUNCTIONS.keys()),
                index=list(SPENDING_FUNCTIONS.keys()).index("obrien_fleming"))

    st.markdown("##### Optional: check-in cadence")
    weekly_traffic = st.number_input(
        "Expected total weekly traffic into this test (control + all variants combined)",
        min_value=0, value=0, step=100,
        help="Used only to suggest how often to actually check in on this test once it's live -- it "
             "doesn't affect the design itself. Leave at 0 to skip. Assumes roughly equal traffic "
             "allocation across arms, same assumption the Monitor page's info-fraction calc makes.",
    )

    submitted = st.form_submit_button("Compute design", type="primary")

if submitted:
    inputs = DesignInputs(
        metric_type=metric_type, baseline=baseline, mde=mde, mde_is_relative=mde_is_relative,
        alpha=alpha, power=power, n_looks=n_looks, spending_function=spending_function,
        futility=futility, futility_spending_function=futility_spending_function, std_dev=std_dev,
        sides=sides, n_variants=int(n_variants),
    )
    try:
        result = design(inputs)
    except Exception as e:
        st.error(f"Couldn't compute a design with these inputs: {e}")
        st.stop()

    st.session_state["design_result"] = result
    st.session_state["design_inputs"] = inputs
    st.session_state["weekly_traffic"] = weekly_traffic
    # A fresh design invalidates any interim data entered against a previous one.
    st.session_state.pop("interim_df", None)

if "design_result" in st.session_state:
    result = st.session_state["design_result"]
    inp = result.inputs

    st.markdown("### Result")
    if inp.n_variants > 1 or inp.sides == "two":
        st.caption(
            f"Per-comparison alpha after Bonferroni ({inp.n_variants} variant(s)): "
            f"**{inp.alpha_per_comparison:.4f}**"
            + (f"  |  per-tail alpha used in the boundary calc (two-sided): **{inp.alpha_tail:.4f}**"
               if inp.sides == "two" else "")
        )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Fixed-horizon N / arm", f"{result.n_fixed_per_arm:,}")
    m2.metric("Group sequential max N / arm", f"{result.n_max_per_arm:,}",
              delta=f"{100 * (result.inflation_factor - 1):+.1f}% vs fixed", delta_color="inverse")
    m3.metric("Expected N / arm if no effect", f"{result.expected_n_under_h0:,.0f}",
              help="Average sample size if the null is true (H0) -- most of these tests stop early for futility.")
    m4.metric("Expected N / arm if true effect", f"{result.expected_n_under_h1:,.0f}",
              help="Average sample size if the design's true effect holds (H1).")

    st.markdown("#### Boundary table")
    table = {
        "Look": list(range(1, inp.n_looks + 1)),
        "Info fraction": [f"{t:.2f}" for t in result.t],
        "N / arm at this look": [int(round(t * result.n_max_per_arm)) for t in result.t],
    }
    if result.lower_efficacy is not None:
        table["Lower Z (significant loss)"] = [f"{z:.3f}" for z in result.lower_efficacy.bounds]
    table["Futility Z"] = ([f"{z:.3f}" for z in result.futility.bounds] if result.futility
                            else ["n/a"] * inp.n_looks)
    table["Efficacy Z (win)"] = [f"{z:.3f}" for z in result.efficacy.bounds]
    st.dataframe(table, width="stretch", hide_index=True)

    st.markdown("#### Boundary chart")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    fig.patch.set_facecolor("#FAF7F2")
    ax.set_facecolor("#FAF7F2")
    ax.plot(result.t, result.efficacy.bounds, color="#D16A0F", marker="o", linewidth=2, label="Efficacy boundary (win)")
    if result.lower_efficacy is not None:
        ax.plot(result.t, result.lower_efficacy.bounds, color="#C2381E", marker="o", linewidth=2,
                label="Lower boundary (significant loss)")
    if result.futility is not None:
        ax.plot(result.t, result.futility.bounds, color="#6C5F54", marker="o", linewidth=2,
                linestyle="--", label="Futility boundary")
        ax.fill_between(result.t, result.futility.bounds, result.efficacy.bounds,
                         color="#F1ECE5", alpha=0.6, label="Continue region")
    ax.axhline(0, color="#C9C0B7", linewidth=1)
    ax.set_xlabel("Information fraction")
    ax.set_ylabel("Z statistic")
    title = "Stopping boundaries"
    if inp.n_variants > 1:
        title += f"  ({inp.n_variants} variants, Bonferroni-adjusted)"
    ax.set_title(title, fontsize=13, fontweight="bold", color="#14100D", loc="left")
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.legend(frameon=False)
    st.pyplot(fig)

    st.info("This design is now available on the **Monitor a test** page to compare live results against.")

    weekly_traffic = st.session_state.get("weekly_traffic", 0)
    if weekly_traffic and weekly_traffic > 0:
        cadence = suggest_monitoring_cadence(result, weekly_traffic_total=weekly_traffic)
        st.markdown("#### Suggested check-in cadence")
        cc1, cc2 = st.columns(2)
        cc1.metric("Projected time to max sample", f"{cadence.projected_weeks_to_max_n:.1f} weeks")
        cc2.metric("Suggested check-in interval", cadence.suggested_interval_label,
                   help=f"~every {cadence.suggested_interval_days:.1f} days, spreading "
                        f"{inp.n_looks} looks evenly across the projected duration.")
        st.caption(
            f"This is a planning aid, not a statistical requirement -- boundaries are recomputed for "
            f"whatever information fraction you actually observe, so drifting from this schedule "
            f"doesn't break error control. Two things worth following anyway: skip the first check "
            f"until at least {cadence.first_check_after_days:.0f} days in (~"
            f"{100 * cadence.first_check_info_fraction:.0f}% information) -- with O'Brien-Fleming "
            f"spending, anything before that is too conservative to stop on and mostly just noise -- "
            f"and try not to check MUCH more often than this, since more looks than planned erodes the "
            f"sample-size efficiency the design was calibrated for and invites reading too much into "
            f"an informal peek between formal ones."
        )
    else:
        st.caption(
            "Enter your expected weekly traffic above (before computing the design) to get a suggested "
            "check-in cadence for this test."
        )

    with st.expander("Full text summary"):
        st.code(summarize(result))

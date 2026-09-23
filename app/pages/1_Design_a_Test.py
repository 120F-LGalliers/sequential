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
                 "sample size per variant goes up accordingly.",
        )
    with c2:
        alpha = st.number_input("Overall false-positive budget (alpha)", min_value=0.001,
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
             "allocation across variants, same assumption the Monitor page's info-fraction calc makes.",
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

    weekly_traffic = st.session_state.get("weekly_traffic", 0)
    cadence = (suggest_monitoring_cadence(result, weekly_traffic_total=weekly_traffic)
               if weekly_traffic and weekly_traffic > 0 else None)
    total_days = cadence.projected_weeks_to_max_n * 7 if cadence is not None else None

    st.markdown("### Result")
    if inp.n_variants > 1 or inp.sides == "two":
        st.caption(
            f"Per-comparison alpha after Bonferroni ({inp.n_variants} variant(s)): "
            f"**{inp.alpha_per_comparison:.4f}**"
            + (f"  |  per-tail alpha used in the boundary calc (two-sided): **{inp.alpha_tail:.4f}**"
               if inp.sides == "two" else "")
        )

    st.markdown("#### Sample size needed")
    st.caption(
        "All four numbers below are **sample size per variant** -- each variant needs roughly this "
        "many people/sessions, and so does control (not a total across the whole test). They answer "
        "slightly different questions, which is why there are four rather than one -- the chart makes "
        "that easier to see at a glance than the numbers alone."
    )

    fig, ax = plt.subplots(figsize=(9, 3.2))
    fig.patch.set_facecolor("#FAF7F2")
    ax.set_facecolor("#FAF7F2")
    points = [
        (result.n_fixed_per_arm, "Standard test\n(no interim looks)", "#6C5F54"),
        (result.expected_n_under_h0, "Typical, if no\nreal effect", "#2C6FB5"),
        (result.expected_n_under_h1, "Typical, if the\neffect is real", "#D16A0F"),
        (result.n_max_per_arm, "Sequential max\n(plan capacity for this)", "#C2381E"),
    ]
    points.sort(key=lambda p: p[0])
    track_max = result.n_max_per_arm
    ax.barh([0], [track_max], height=0.04, color="#F1ECE5", zorder=1)
    for i, (value, label, color) in enumerate(points):
        above = i % 2 == 0
        y_stem, y_text = (0.5, 0.85) if above else (-0.5, -0.85)
        va = "bottom" if above else "top"
        ax.plot([value, value], [0, y_stem], color=color, linewidth=1.3, zorder=2)
        ax.scatter([value], [0], color=color, s=50, zorder=3, edgecolor="white", linewidth=1)
        ax.annotate(f"{label}\n{value:,.0f}", xy=(value, y_text), ha="center", va=va,
                    fontsize=7.5, color="#14100D", linespacing=1.3)
    ax.set_ylim(-1.7, 1.7)
    ax.set_yticks([])
    ax.set_xlim(0, track_max * 1.12)
    ax.set_xlabel("Sample size per variant", fontsize=9)
    ax.tick_params(axis="x", labelsize=8)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    st.pyplot(fig)

    if cadence is not None:
        st.markdown("##### Time this saves, at your expected traffic")
        tc1, tc2, tc3 = st.columns(3)
        tc1.metric("Saved, if no real effect", f"{cadence.days_saved_if_no_effect:.0f} days sooner",
                   help="How much sooner this test typically finishes, vs. running all the way to the "
                        "sequential max, if there's truly no difference between variant and control.")
        tc2.metric("Saved, if the effect is real", f"{cadence.days_saved_if_real_effect:.0f} days sooner",
                   help="How much sooner this test typically finishes, vs. running all the way to the "
                        "sequential max, if the true effect matches your MDE.")
        tc3.metric("Extra time, worst case", f"+{cadence.extra_days_max_vs_standard:.0f} days",
                   delta=f"vs. a standard test", delta_color="off",
                   help="How much LONGER this design takes in its worst case (running all the way to "
                        "the sequential max) compared to a standard, single-look test. This is the "
                        "downside you're trading for the chance to stop early in the two scenarios above.")
        st.caption(
            "A good one-liner for stakeholders: this design usually finishes noticeably faster than a "
            "standard test, and only very rarely takes a little longer -- worth showing alongside the "
            "chart above when explaining why a sequential design is worth the extra complexity."
        )
    else:
        st.caption(
            "Enter your expected weekly traffic above (before computing the design) to see how much "
            "time each scenario above actually saves -- useful for explaining the trade-off to the team."
        )

    with st.expander("What do these four numbers mean?"):
        st.markdown(
            "- **Standard test** — what you'd need per variant with a traditional, single-look test "
            "(no interim analyses along the way). This is the baseline the sequential design's extra "
            "sample-size cost is measured against.\n"
            "- **Sequential max** — the most you'd EVER need per variant with this sequential design, "
            "if it runs all the way to the final planned look without stopping early. This is the "
            "number to plan traffic capacity for before you start.\n"
            "- **Typical, if no real effect** — the average sample size per variant across many "
            "hypothetical repeats of this test, assuming there's truly no difference between variant "
            "and control. Most such tests stop early for futility, so this is usually well under the "
            "sequential max.\n"
            "- **Typical, if the effect is real** — the average sample size per variant assuming the "
            "true effect matches your MDE. Most such tests stop early for efficacy, so this too is "
            "usually well under the sequential max.\n\n"
            "In short: **plan for the sequential max**, but expect most tests to finish sooner --"
            " that's the whole point of a sequential design over a standard one."
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Standard test", f"{result.n_fixed_per_arm:,}")
        m2.metric("Sequential max", f"{result.n_max_per_arm:,}",
                  delta=f"{100 * (result.inflation_factor - 1):+.1f}% vs standard test", delta_color="inverse")
        m3.metric("Typical, no real effect", f"{result.expected_n_under_h0:,.0f}")
        m4.metric("Typical, real effect", f"{result.expected_n_under_h1:,.0f}")

    st.markdown("#### Boundary table")
    table = {
        "Look": list(range(1, inp.n_looks + 1)),
        "% of max sample": [f"{100 * t:.0f}%" for t in result.t],
        "Sample size at this look (per variant)": [int(round(t * result.n_max_per_arm)) for t in result.t],
    }
    if result.lower_efficacy is not None:
        table["Lower Z (significant loss)"] = [f"{z:.3f}" for z in result.lower_efficacy.bounds]
    table["Futility Z"] = ([f"{z:.3f}" for z in result.futility.bounds] if result.futility
                            else ["n/a"] * inp.n_looks)
    table["Efficacy Z (win)"] = [f"{z:.3f}" for z in result.efficacy.bounds]
    st.dataframe(table, width="stretch", hide_index=True)

    col_cadence, col_chart = st.columns([1.1, 0.9])

    with col_cadence:
        st.markdown("#### Suggested check-in cadence")
        if cadence is not None:
            cc1, cc2 = st.columns(2)
            cc1.metric("Time to max sample", f"{cadence.projected_weeks_to_max_n:.1f} wks")
            cc2.metric("Check-in interval", cadence.suggested_interval_label,
                       help=f"~every {cadence.suggested_interval_days:.1f} days, spreading "
                            f"{inp.n_looks} looks evenly across the projected duration.")
            st.markdown(
                f"A planning aid, not a statistical requirement -- boundaries are recomputed for "
                f"whatever information fraction you actually observe, so drifting from this doesn't "
                f"break error control. Skip the first check until at least "
                f"{cadence.first_check_after_days:.0f} days in (~"
                f"{100 * cadence.first_check_info_fraction:.0f}% information -- earlier is mostly "
                f"noise with O'Brien-Fleming spending), and try not to check much more often than "
                f"shown, since more looks than planned erodes the sample-size efficiency the design "
                f"was calibrated for. The chart on the right marks each planned check-in day."
            )
        else:
            st.markdown(
                "Enter your expected weekly traffic above (before computing the design) to get a "
                "suggested check-in cadence, and see planned check-in days marked on the boundary "
                "chart."
            )

    with col_chart:
        st.markdown("#### Boundary chart")
        fig, ax = plt.subplots(figsize=(6.2, 3.8))
        fig.patch.set_facecolor("#FAF7F2")
        ax.set_facecolor("#FAF7F2")
        ax.plot(result.t, result.efficacy.bounds, color="#D16A0F", marker="o", markersize=5,
                linewidth=1.8, label="Efficacy (win)")
        if result.lower_efficacy is not None:
            ax.plot(result.t, result.lower_efficacy.bounds, color="#C2381E", marker="o", markersize=5,
                    linewidth=1.8, label="Lower (loss)")
        if result.futility is not None:
            ax.plot(result.t, result.futility.bounds, color="#6C5F54", marker="o", markersize=5,
                    linewidth=1.8, linestyle="--", label="Futility")
            ax.fill_between(result.t, result.futility.bounds, result.efficacy.bounds,
                             color="#F1ECE5", alpha=0.6, label="Continue")
        ax.axhline(0, color="#C9C0B7", linewidth=1)

        if total_days is not None:
            ax.set_xticks(result.t)
            ax.set_xticklabels([f"{t:.2f}\nDay {round(t * total_days)}" for t in result.t], fontsize=6.5)
            ax.set_xlabel("Information fraction  /  projected check-in day", fontsize=8)
        else:
            ax.set_xlabel("Information fraction", fontsize=9)
            ax.tick_params(axis="x", labelsize=8)
        ax.set_ylabel("Z statistic", fontsize=9)
        ax.tick_params(axis="y", labelsize=8)
        title = "Stopping boundaries"
        if inp.n_variants > 1:
            title += f" ({inp.n_variants} variants)"
        ax.set_title(title, fontsize=10.5, fontweight="bold", color="#14100D", loc="left")
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        ax.legend(frameon=False, fontsize=7)
        fig.tight_layout()
        st.pyplot(fig)

    st.info("This design is now available on the **Monitor a test** page to compare live results against.")

    with st.expander("Full text summary"):
        st.code(summarize(result))

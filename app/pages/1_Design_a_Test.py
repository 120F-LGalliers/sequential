"""Design-stage calculator page."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, so `import engine`/`import app` work

import matplotlib.pyplot as plt
import streamlit as st

from app._theme import apply_brand, eyebrow
from engine.design import DesignInputs, design, summarize
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
            min_value=0.0001, value=0.10 if metric_type == "binary" else 50.0, format="%.4f")
        std_dev = None
        if metric_type == "continuous":
            std_dev = st.number_input("Per-user standard deviation", min_value=0.0001, value=30.0)
        mde_is_relative = st.radio("MDE type", ["relative", "absolute"], horizontal=True) == "relative"
        mde = st.number_input(
            "Minimum detectable effect" + (" (e.g. 0.10 = +10% relative)" if mde_is_relative else " (absolute units)"),
            value=0.10, format="%.4f")
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
                                      "across the whole test, not per comparison.")
        power = st.number_input("Target power (1 - beta)", min_value=0.5, max_value=0.999, value=0.80)
        n_looks = st.slider("Planned number of interim looks", min_value=2, max_value=12, value=5)
        spending_function = st.selectbox("Efficacy spending function", list(SPENDING_FUNCTIONS.keys()),
                                          index=list(SPENDING_FUNCTIONS.keys()).index("obrien_fleming"),
                                          help="O'Brien-Fleming (recommended default): conservative early on, "
                                               "minimal sample-size cost. Pocock: spends error more evenly, "
                                               "lets you stop earlier on strong results, costs more sample size.")
        futility = st.checkbox("Include a (non-binding) futility stopping rule", value=True)
        futility_spending_function = spending_function
        if futility:
            futility_spending_function = st.selectbox(
                "Futility spending function", list(SPENDING_FUNCTIONS.keys()),
                index=list(SPENDING_FUNCTIONS.keys()).index("obrien_fleming"))

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

    with st.expander("Full text summary"):
        st.code(summarize(result))

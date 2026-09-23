"""
120F Group Sequential Testing — Home.

Auth/SSO is handled by existing 120F infrastructure (not this app) --
nothing here implements login. Swap this file's plumbing (imports, page
config, deployment entrypoint) for whatever your existing Streamlit apps
use once you've shared one to match conventions against.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, so `import engine` works

import streamlit as st

from app._theme import apply_brand, eyebrow

st.set_page_config(page_title="120F Sequential Testing", layout="wide")
apply_brand()

eyebrow("120F internal tool")
st.title("Group sequential testing")
st.markdown(
    "A calculator and live monitor for group sequential (AGILE-style) A/B tests -- "
    "design a test with pre-planned efficacy/futility stopping rules, then log interim "
    "results as they come in to get a continue/stop read at any point, without inflating "
    "the false-positive rate from repeated peeking."
)

st.warning(
    "**v0.1 -- statistical core only, not yet cross-validated against a second reference "
    "implementation (R's rpact/gsDesign).** Validated so far via internal self-consistency "
    "checks and independent Monte Carlo simulation (see the engine's `validate.py`). Treat "
    "outputs as a strong first pass, not yet a client-facing final source of truth -- see "
    "the project doc for the sign-off plan.",
    icon="⚠️",
)

st.markdown("### How to use this")
col1, col2 = st.columns(2)
with col1:
    st.markdown("**1. Design a test**")
    st.markdown(
        "Enter your baseline metric, minimum detectable effect, alpha/power, and how many "
        "interim looks you want. Get back the maximum sample size and the boundary table."
    )
with col2:
    st.markdown("**2. Monitor a test**")
    st.markdown(
        "Once a test is live, log cumulative sample size and conversions/mean per variant at "
        "each check-in (manual entry for now -- see below). Get a continue/stop read, "
        "recomputed for the actual information fraction observed."
    )

st.markdown("---")
st.markdown("### Before you start")
st.error(
    "**Nothing is saved between visits.** There's no database behind this tool yet -- a design "
    "and any interim data you've entered both disappear on refresh or when the app restarts. "
    "Keep your own record (e.g. the report you're pulling numbers from) and re-enter as needed.",
    icon="💾",
)
st.caption(
    "Data entry is manual in this version: 120F doesn't currently have server-to-server "
    "credentials for Adobe Analytics, so there's no automated pull yet. Numbers are typed "
    "in at the Monitor page from whatever report you're already pulling."
)

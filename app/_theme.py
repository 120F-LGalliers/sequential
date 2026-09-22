"""Light-touch 120Feet brand styling for the Streamlit app.

This is a v0.1 internal analyst tool, not a client-facing deliverable, so
this deliberately stays to the "quick/internal" end of the brand skill's
guidance: the Streamlit theme (.streamlit/config.toml) carries the real
weight (brand orange as primaryColor, warm off-white surfaces, ink text),
and this file just layers on the brand typeface and a couple of small
touches Streamlit's theme system can't reach (headings, the eyebrow label
style). Swap in your own conventions freely once you've seen this against
an existing 120F Streamlit app.
"""

import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo+Black&family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap');

html, body, [class*="css"] {
  font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
h1, h2, h3, h4, h5, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
  font-family: 'Manrope', sans-serif;
  font-weight: 800;
  color: #14100D;
  letter-spacing: -0.02em;
}
code, pre, .stCode {
  font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace !important;
}
.eyebrow {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #D16A0F;
}
/* Primary action buttons -> brand orange, flat, no gradient */
.stButton > button[kind="primary"] {
  background-color: #D16A0F;
  border-color: #D16A0F;
}
.stButton > button[kind="primary"]:hover {
  background-color: #B85B0A;
  border-color: #B85B0A;
}
</style>
"""


def apply_brand():
    st.markdown(_CSS, unsafe_allow_html=True)


def eyebrow(text: str):
    st.markdown(f'<div class="eyebrow">{text}</div>', unsafe_allow_html=True)

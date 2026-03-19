"""
Market Discovery - Arbitrage Opportunity Dashboard

Multi-page Streamlit app. Pages live in ui/pages/ and are auto-discovered.
"""

import streamlit as st

st.set_page_config(
    page_title="Market Discovery - Arbitrage Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global session state defaults
if "selected_product_id" not in st.session_state:
    st.session_state.selected_product_id = None

st.title("Market Discovery & PMN")

st.markdown("Select a page from the sidebar to get started.")

st.divider()
st.caption("Market Discovery Dashboard | Real-time arbitrage opportunities")

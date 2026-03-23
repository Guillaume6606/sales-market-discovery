"""
Market Discovery - Arbitrage Opportunity Dashboard

Multi-page Streamlit app with grouped navigation.
"""

import streamlit as st

from ui.lib.config import APP_VERSION
from ui.lib.theme import inject_global_css

st.set_page_config(
    page_title="Market Discovery",
    page_icon=":material/trending_up:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Global session state defaults
if "selected_product_id" not in st.session_state:
    st.session_state.selected_product_id = None

# Inject global CSS
inject_global_css()

# ---------------------------------------------------------------------------
# Navigation with grouped pages
# ---------------------------------------------------------------------------
pg = st.navigation(
    {
        "": [
            st.Page("pages/0_Home.py", title="Home", icon=":material/home:", default=True),
        ],
        "Operator": [
            st.Page("pages/1_Discovery.py", title="Discovery", icon=":material/search:"),
            st.Page("pages/2_Listing_Explorer.py", title="Listings", icon=":material/list_alt:"),
            st.Page("pages/6_Alerts.py", title="Alerts", icon=":material/notifications_active:"),
        ],
        "Admin": [
            st.Page("pages/3_Product_Setup.py", title="Products", icon=":material/settings:"),
            st.Page("pages/4_Import_Data.py", title="Import", icon=":material/cloud_download:"),
        ],
        "Ops": [
            st.Page("pages/5_Health.py", title="Health", icon=":material/monitor_heart:"),
        ],
    },
    expanded=True,
)

# Sidebar branding
with st.sidebar:
    st.divider()
    st.caption(f"Market Discovery v{APP_VERSION}")

pg.run()

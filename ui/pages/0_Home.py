"""Home: dashboard overview with KPIs, quick actions, and recent alerts."""

import streamlit as st

from ui.lib.api import (
    fetch_alert_events,
    fetch_analytics,
    fetch_health_overview,
    fetch_ingestion_health,
)
from ui.lib.components import kpi_row
from ui.lib.formatters import relative_time
from ui.lib.theme import status_badge

# ---------------------------------------------------------------------------
# System status header
# ---------------------------------------------------------------------------
overview = fetch_health_overview()
sys_status = overview.get("system_status", "gray") if overview else "gray"

st.markdown("# Market Discovery")
st.markdown(
    f"Arbitrage Detection Platform &nbsp; {status_badge(sys_status)}",
    unsafe_allow_html=True,
)
st.divider()

# ---------------------------------------------------------------------------
# KPI summary strip
# ---------------------------------------------------------------------------
analytics = fetch_analytics()
if analytics:
    kpi_row(
        [
            {"label": "Opportunities", "value": analytics.get("opportunities_count", 0)},
            {"label": "Products", "value": analytics.get("total_products", 0)},
            {"label": "Active Listings", "value": analytics.get("active_listings", 0)},
            {"label": "Sold Items", "value": analytics.get("sold_items", 0)},
            {"label": "Recent (24h)", "value": analytics.get("recent_observations_24h", 0)},
        ]
    )
else:
    st.info("Analytics data unavailable. Is the backend running?")

st.divider()

# ---------------------------------------------------------------------------
# Quick actions
# ---------------------------------------------------------------------------
st.markdown("### Quick Actions")
qa1, qa2, qa3 = st.columns(3)

with qa1:
    with st.container(border=True):
        st.markdown("**Find Opportunities**")
        st.caption("Browse arbitrage deals with advanced filters")
        st.page_link("pages/1_Discovery.py", label="Open Discovery", icon=":material/search:")

# Pre-fetch alert events once (used by both Quick Actions count and Recent Alerts feed)
_alert_events_data = fetch_alert_events(limit=5)
_alert_events: list = _alert_events_data.get("events", []) if _alert_events_data else []
_alert_total: int = _alert_events_data.get("total", 0) if _alert_events_data else 0

with qa2:
    with st.container(border=True):
        st.markdown("**Review Alerts**")
        st.caption(f"{_alert_total} alert events total")
        st.page_link(
            "pages/6_Alerts.py", label="Open Alerts", icon=":material/notifications_active:"
        )

with qa3:
    with st.container(border=True):
        st.markdown("**System Health**")
        st.markdown(
            f"Status: {status_badge(sys_status)}",
            unsafe_allow_html=True,
        )
        st.page_link("pages/5_Health.py", label="Open Health", icon=":material/monitor_heart:")

st.divider()

# ---------------------------------------------------------------------------
# Recent alerts feed
# ---------------------------------------------------------------------------
col_alerts, col_connectors = st.columns([3, 2])

with col_alerts:
    st.markdown("### Recent Alerts")
    events = _alert_events

    if events:
        for evt in events:
            product_id = evt.get("product_id", "?")[:8]
            sent_at = relative_time(evt.get("sent_at"))
            suppressed = evt.get("suppressed", False)

            label = f"Product `{product_id}` | {sent_at}"
            if suppressed:
                label += " *(suppressed)*"

            st.markdown(f"- {label}")
    else:
        st.caption("No recent alerts")

# ---------------------------------------------------------------------------
# Connector status mini-cards
# ---------------------------------------------------------------------------
with col_connectors:
    st.markdown("### Connector Status")
    ingestion_health = fetch_ingestion_health()

    if ingestion_health:
        for conn in ingestion_health:
            source = conn.get("source", "?").title()
            sr24 = conn.get("success_rate_24h")

            # Determine status color
            if sr24 is None:
                conn_status = "gray"
            elif sr24 >= 0.9:
                conn_status = "green"
            elif sr24 >= 0.5:
                conn_status = "yellow"
            else:
                conn_status = "red"

            rate_str = f"{sr24:.0%}" if sr24 is not None else "N/A"
            st.markdown(
                f"{status_badge(conn_status, source)} &nbsp; Success 24h: **{rate_str}**",
                unsafe_allow_html=True,
            )
    else:
        st.caption("Connector data unavailable")

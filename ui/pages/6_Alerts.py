"""Alert Management: rules, events, feedback, and precision metrics."""

import pandas as pd
import streamlit as st

from ui.lib.api import (
    api_delete,
    api_post,
    fetch_alert_events,
    fetch_alert_precision,
    fetch_alert_rules,
)
from ui.lib.formatters import relative_time

st.header("Alert Management")

# ---------------------------------------------------------------------------
# Precision dashboard
# ---------------------------------------------------------------------------
st.subheader("Alert Precision (Last 30 Days)")
precision = fetch_alert_precision(days=30)

if precision:
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Total Alerts", precision.get("total_alerts", 0))
    fr = precision.get("feedback_rate")
    p2.metric("Feedback Rate", f"{fr:.0%}" if fr is not None else "N/A")
    prec = precision.get("precision")
    p3.metric("Precision", f"{prec:.0%}" if prec is not None else "N/A")
    p4.metric("Purchased", precision.get("purchased_count", 0))
else:
    st.info("No alert precision data available")

if st.button("Refresh", key="alert_refresh"):
    fetch_alert_rules.clear()
    fetch_alert_events.clear()
    fetch_alert_precision.clear()
    st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Alert rules table + CRUD
# ---------------------------------------------------------------------------
st.subheader("Alert Rules")
rules = fetch_alert_rules()

if rules:
    rules_df = pd.DataFrame(rules)
    display_cols = [
        "name",
        "threshold_pct",
        "min_margin_abs",
        "min_liquidity_score",
        "channels",
    ]
    display_cols = [c for c in display_cols if c in rules_df.columns]
    st.dataframe(rules_df[display_cols], hide_index=True, use_container_width=True)

    # Per-rule actions
    selected_rule_name = st.selectbox(
        "Select Rule",
        [r["name"] for r in rules],
        key="rule_action_select",
    )
    selected_rule = next((r for r in rules if r["name"] == selected_rule_name), None)

    if selected_rule:
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            if st.button("Test Rule"):
                with st.spinner("Testing..."):
                    try:
                        r = api_post(f"/alerts/test/{selected_rule['rule_id']}", timeout=15.0)
                        if r.status_code == 200:
                            result = r.json()
                            count = result.get("match_count", 0)
                            st.success(f"Rule matches {count} listing(s)")
                            matches = result.get("matches", [])
                            if matches:
                                st.dataframe(
                                    pd.DataFrame(matches),
                                    hide_index=True,
                                    use_container_width=True,
                                )
                        else:
                            st.error(f"Test failed: {r.text}")
                    except Exception as exc:
                        st.error(f"Error: {exc}")

        with ac3:
            if st.button("Delete Rule", type="secondary"):
                try:
                    r = api_delete(f"/alerts/rules/{selected_rule['rule_id']}")
                    if r.status_code == 204:
                        st.success("Rule deleted")
                        fetch_alert_rules.clear()
                        st.rerun()
                    else:
                        st.error(f"Delete failed: {r.text}")
                except Exception as exc:
                    st.error(f"Error: {exc}")
else:
    st.info("No alert rules configured yet")

# Create rule form
st.markdown("#### Create New Rule")
with st.form("create_rule", clear_on_submit=True):
    rule_name = st.text_input("Rule Name *", key="new_rule_name")

    rc1, rc2 = st.columns(2)
    with rc1:
        rule_threshold = st.number_input(
            "Threshold % (margin below PMN)",
            min_value=0.0,
            max_value=100.0,
            value=10.0,
            step=5.0,
            key="new_rule_threshold",
        )
    with rc2:
        rule_margin_abs = st.number_input(
            "Min Margin Absolute",
            min_value=0.0,
            value=0.0,
            step=5.0,
            key="new_rule_margin_abs",
        )

    rc3, rc4 = st.columns(2)
    with rc3:
        rule_liquidity = st.number_input(
            "Min Liquidity Score",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.1,
            key="new_rule_liquidity",
        )
    with rc4:
        rule_channels = st.multiselect(
            "Channels",
            options=["telegram"],
            default=["telegram"],
            key="new_rule_channels",
        )

    if st.form_submit_button("Create Rule", type="primary"):
        if not rule_name.strip():
            st.error("Rule name is required")
        else:
            payload = {
                "name": rule_name.strip(),
                "threshold_pct": rule_threshold if rule_threshold > 0 else None,
                "min_margin_abs": rule_margin_abs if rule_margin_abs > 0 else None,
                "min_liquidity_score": (rule_liquidity if rule_liquidity > 0 else None),
                "channels": rule_channels,
            }
            try:
                r = api_post("/alerts/rules", json=payload, timeout=10.0)
                if r.status_code == 201:
                    st.success(f"Rule '{rule_name}' created!")
                    fetch_alert_rules.clear()
                    st.rerun()
                else:
                    st.error(f"Creation failed: {r.text}")
            except Exception as exc:
                st.error(f"Error: {exc}")

st.divider()

# ---------------------------------------------------------------------------
# Alert events timeline
# ---------------------------------------------------------------------------
st.subheader("Recent Alert Events")

if "alert_events_page" not in st.session_state:
    st.session_state.alert_events_page = 0

PAGE_SIZE = 50
offset = st.session_state.alert_events_page * PAGE_SIZE

# Filters
ef1, ef2 = st.columns(2)
with ef1:
    event_rule_filter = st.selectbox(
        "Filter by Rule",
        ["All"] + [r["name"] for r in rules] if rules else ["All"],
        key="event_rule_filter",
    )
with ef2:
    event_product_filter = st.text_input(
        "Product ID", placeholder="Filter by product ID...", key="event_product_filter"
    )

rule_id_filter = None
if event_rule_filter != "All" and rules:
    rule_id_filter = next((r["rule_id"] for r in rules if r["name"] == event_rule_filter), None)

events_data = fetch_alert_events(
    rule_id=rule_id_filter,
    product_id=event_product_filter if event_product_filter else None,
    limit=PAGE_SIZE,
    offset=offset,
)

events = events_data.get("events", [])
events_total = events_data.get("total", 0)

if events:
    for evt in events:
        alert_id = evt.get("alert_id")
        sent_at = relative_time(evt.get("sent_at"))
        product_id = evt.get("product_id", "?")[:8]
        suppressed = evt.get("suppressed", False)

        rule_name_display = "?"
        if rules:
            match = next((r for r in rules if r["rule_id"] == evt.get("rule_id")), None)
            if match:
                rule_name_display = match["name"]

        label = f"Alert #{alert_id} | {rule_name_display} | Product {product_id} | {sent_at}"
        if suppressed:
            label += " (suppressed)"

        with st.container(border=True):
            st.caption(label)

            # Feedback buttons
            fb1, fb2, fb3 = st.columns(3)
            with fb1:
                if st.button("Interested", key=f"fb_int_{alert_id}", use_container_width=True):
                    try:
                        r = api_post(
                            f"/alerts/events/{alert_id}/feedback",
                            json={"feedback": "interested"},
                        )
                        if r.status_code == 201:
                            st.success("Feedback saved")
                            fetch_alert_events.clear()
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"Error: {exc}")

            with fb2:
                if st.button(
                    "Not Interested",
                    key=f"fb_not_{alert_id}",
                    use_container_width=True,
                ):
                    try:
                        r = api_post(
                            f"/alerts/events/{alert_id}/feedback",
                            json={"feedback": "not_interested"},
                        )
                        if r.status_code == 201:
                            st.success("Feedback saved")
                            fetch_alert_events.clear()
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"Error: {exc}")

            with fb3:
                if st.button(
                    "Purchased",
                    key=f"fb_purch_{alert_id}",
                    use_container_width=True,
                ):
                    try:
                        r = api_post(
                            f"/alerts/events/{alert_id}/feedback",
                            json={"feedback": "purchased"},
                        )
                        if r.status_code == 201:
                            st.success("Feedback saved")
                            fetch_alert_events.clear()
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"Error: {exc}")

    # Pagination
    total_pages = max(1, (events_total + PAGE_SIZE - 1) // PAGE_SIZE)
    current_page = st.session_state.alert_events_page

    ep1, ep2, ep3 = st.columns([1, 2, 1])
    with ep1:
        if st.button("Prev", disabled=current_page == 0, key="events_prev"):
            st.session_state.alert_events_page = current_page - 1
            fetch_alert_events.clear()
            st.rerun()
    with ep2:
        st.caption(f"Page {current_page + 1} of {total_pages} ({events_total} total)")
    with ep3:
        if st.button("Next", disabled=current_page >= total_pages - 1, key="events_next"):
            st.session_state.alert_events_page = current_page + 1
            fetch_alert_events.clear()
            st.rerun()
else:
    st.info("No alert events found")

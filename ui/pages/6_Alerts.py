"""Alert Management: rules, events, feedback, and precision metrics."""

from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.lib.api import (
    api_delete,
    api_post,
    fetch_alert_events,
    fetch_alert_precision,
    fetch_alert_rules,
)
from ui.lib.components import confirm_action, kpi_row, paginator
from ui.lib.formatters import relative_time
from ui.lib.theme import COLORS, PLOTLY_LAYOUT, status_badge

PAGE_SIZE = 50

# ---------------------------------------------------------------------------
# Local helper
# ---------------------------------------------------------------------------


def _submit_feedback(alert_id: int, feedback: str) -> None:
    """POST feedback for a single alert event and clear the events cache.

    Args:
        alert_id: Primary key of the alert event.
        feedback: One of 'interested', 'not_interested', 'purchased'.
    """
    try:
        r = api_post(
            f"/alerts/events/{alert_id}/feedback",
            json={"feedback": feedback},
        )
        if r.status_code == 201:
            st.success("Feedback saved")
            fetch_alert_events.clear()
        else:
            st.error(f"Failed: {r.text}")
    except Exception as exc:
        st.error(f"Error: {exc}")


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown("# Alert Management")
st.caption("Rules, events, feedback, and precision metrics")

if st.button("Refresh", key="alert_refresh"):
    fetch_alert_rules.clear()
    fetch_alert_events.clear()
    fetch_alert_precision.clear()
    st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Precision dashboard
# ---------------------------------------------------------------------------

st.subheader("Alert Precision (Last 30 Days)")
precision: dict[str, Any] | None = fetch_alert_precision(days=30)

if precision:
    total_alerts: int = precision.get("total_alerts", 0)
    fr: float | None = precision.get("feedback_rate")
    prec: float | None = precision.get("precision")
    purchased: int = precision.get("purchased_count", 0)

    kpi_row(
        [
            {"label": "Total Alerts", "value": total_alerts},
            {"label": "Feedback Rate", "value": f"{fr:.0%}" if fr is not None else "N/A"},
            {"label": "Precision", "value": f"{prec:.0%}" if prec is not None else "N/A"},
            {"label": "Purchased", "value": purchased},
        ]
    )

    # ------------------------------------------------------------------
    # Feedback funnel chart
    # ------------------------------------------------------------------
    with_feedback: int = precision.get("with_feedback_count", round(total_alerts * (fr or 0)))
    interested: int = precision.get("interested_count", round(with_feedback * (prec or 0)))

    funnel_labels = ["Total Alerts", "With Feedback", "Interested", "Purchased"]
    funnel_values = [total_alerts, with_feedback, interested, purchased]

    fig = go.Figure(
        go.Bar(
            x=funnel_values,
            y=funnel_labels,
            orientation="h",
            marker_color=[
                COLORS["primary"],
                COLORS["primary_light"],
                COLORS["warning"],
                COLORS["success"],
            ],
            text=[str(v) for v in funnel_values],
            textposition="auto",
        )
    )
    layout = dict(PLOTLY_LAYOUT)
    layout.update(
        {
            "title": {"text": "Feedback Funnel", "font": {"size": 14}},
            "xaxis": {**PLOTLY_LAYOUT.get("xaxis", {}), "title": "Count"},
            "yaxis": {**PLOTLY_LAYOUT.get("yaxis", {}), "autorange": "reversed"},
            "height": 220,
            "margin": {"l": 120, "r": 20, "t": 40, "b": 40},
        }
    )
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("No alert precision data available")

st.divider()

# ---------------------------------------------------------------------------
# Alert rules table + CRUD
# ---------------------------------------------------------------------------

st.subheader("Alert Rules")
rules: list[dict[str, Any]] = fetch_alert_rules()

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

    selected_rule_name: str = st.selectbox(
        "Select Rule",
        [r["name"] for r in rules],
        key="rule_action_select",
    )  # type: ignore[assignment]
    selected_rule: dict[str, Any] | None = next(
        (r for r in rules if r["name"] == selected_rule_name), None
    )

    if selected_rule:
        ac1, _ac2, ac3 = st.columns(3)

        with ac1:
            if st.button("Test Rule", key="test_rule_btn"):
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
            rule_id_to_delete: str = selected_rule["rule_id"]

            def _do_delete() -> None:
                try:
                    resp = api_delete(f"/alerts/rules/{rule_id_to_delete}")
                    if resp.status_code == 204:
                        st.success("Rule deleted")
                        fetch_alert_rules.clear()
                    else:
                        st.error(f"Delete failed: {resp.text}")
                except Exception as exc:
                    st.error(f"Error: {exc}")

            confirm_action(
                key=f"delete_rule_{rule_id_to_delete}",
                label="Delete Rule",
                on_confirm=_do_delete,
                confirm_label="Confirm Delete",
                danger=True,
            )

else:
    st.info("No alert rules configured yet")

# Create rule form
st.markdown("#### Create New Rule")
with st.form("create_rule", clear_on_submit=True):
    rule_name: str = st.text_input("Rule Name *", key="new_rule_name")

    rc1, rc2 = st.columns(2)
    with rc1:
        rule_threshold: float = st.number_input(
            "Threshold % (margin below PMN)",
            min_value=0.0,
            max_value=100.0,
            value=10.0,
            step=5.0,
            key="new_rule_threshold",
        )  # type: ignore[assignment]
    with rc2:
        rule_margin_abs: float = st.number_input(
            "Min Margin Absolute",
            min_value=0.0,
            value=0.0,
            step=5.0,
            key="new_rule_margin_abs",
        )  # type: ignore[assignment]

    rc3, rc4 = st.columns(2)
    with rc3:
        rule_liquidity: float = st.number_input(
            "Min Liquidity Score",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.1,
            key="new_rule_liquidity",
        )  # type: ignore[assignment]
    with rc4:
        rule_channels: list[str] = st.multiselect(
            "Channels",
            options=["telegram"],
            default=["telegram"],
            key="new_rule_channels",
        )

    if st.form_submit_button("Create Rule", type="primary"):
        if not rule_name.strip():
            st.error("Rule name is required")
        else:
            payload: dict[str, Any] = {
                "name": rule_name.strip(),
                "threshold_pct": rule_threshold if rule_threshold > 0 else None,
                "min_margin_abs": rule_margin_abs if rule_margin_abs > 0 else None,
                "min_liquidity_score": rule_liquidity if rule_liquidity > 0 else None,
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

# Filters
ef1, ef2 = st.columns(2)
with ef1:
    filter_options: list[str] = ["All"] + ([r["name"] for r in rules] if rules else [])
    event_rule_filter: str = st.selectbox(
        "Filter by Rule",
        filter_options,
        key="event_rule_filter",
    )  # type: ignore[assignment]
with ef2:
    event_product_filter: str = st.text_input(
        "Product ID", placeholder="Filter by product ID...", key="event_product_filter"
    )

rule_id_filter: str | None = None
if event_rule_filter != "All" and rules:
    rule_id_filter = next((r["rule_id"] for r in rules if r["name"] == event_rule_filter), None)

# Fetch events with current page offset from session state
if "alert_events_page" not in st.session_state:
    st.session_state.alert_events_page = 0
_offset: int = st.session_state.alert_events_page * PAGE_SIZE

events_data: dict[str, Any] = fetch_alert_events(
    rule_id=rule_id_filter,
    product_id=event_product_filter if event_product_filter else None,
    limit=PAGE_SIZE,
    offset=_offset,
)
events: list[dict[str, Any]] = events_data.get("events", [])
events_total: int = events_data.get("total", 0)

# Build a rule lookup for display
rule_by_id: dict[Any, dict[str, Any]] = {r["rule_id"]: r for r in rules} if rules else {}

_FEEDBACK_LABELS: dict[str, str] = {
    "interested": "Interested",
    "not_interested": "Not Interested",
    "purchased": "Purchased",
}

if events:
    for evt in events:
        alert_id: int = evt.get("alert_id")
        sent_at: str = relative_time(evt.get("sent_at"))
        product_id_raw: str = evt.get("product_id", "?")
        suppressed: bool = evt.get("suppressed", False)
        existing_feedback: str | None = evt.get("feedback")

        matched_rule: dict[str, Any] | None = rule_by_id.get(evt.get("rule_id"))
        product_display: str = (
            matched_rule.get("name", product_id_raw[:8]) if matched_rule else product_id_raw[:8]
        )

        suppressed_badge: str = (
            status_badge("red", "Suppressed") if suppressed else status_badge("green", "Active")
        )

        with st.container(border=True):
            header_col, badge_col = st.columns([4, 1])
            with header_col:
                st.caption(
                    f"Alert #{alert_id} | {product_display} | Product ...{product_id_raw[-8:]} "
                    f"| {sent_at}"
                )
            with badge_col:
                st.markdown(suppressed_badge, unsafe_allow_html=True)

            if existing_feedback:
                fb_label = _FEEDBACK_LABELS.get(existing_feedback, existing_feedback)
                st.markdown(
                    f'<span class="badge badge-blue">Feedback: {fb_label}</span>',
                    unsafe_allow_html=True,
                )

            fb1, fb2, fb3 = st.columns(3)
            with fb1:
                if st.button(
                    "Interested",
                    key=f"fb_int_{alert_id}",
                    use_container_width=True,
                    type="primary" if existing_feedback == "interested" else "secondary",
                ):
                    _submit_feedback(alert_id, "interested")
            with fb2:
                if st.button(
                    "Not Interested",
                    key=f"fb_not_{alert_id}",
                    use_container_width=True,
                    type="primary" if existing_feedback == "not_interested" else "secondary",
                ):
                    _submit_feedback(alert_id, "not_interested")
            with fb3:
                if st.button(
                    "Purchased",
                    key=f"fb_purch_{alert_id}",
                    use_container_width=True,
                    type="primary" if existing_feedback == "purchased" else "secondary",
                ):
                    _submit_feedback(alert_id, "purchased")
    # Pagination controls
    paginator("alert_events_page", events_total, PAGE_SIZE)
else:
    st.info("No alert events found")

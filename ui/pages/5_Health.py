"""Health & Observability: system status, connector health, ingestion runs."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.lib.api import (
    fetch_audit_results,
    fetch_computation_status,
    fetch_health_overview,
    fetch_ingestion_health,
    fetch_ingestion_runs,
    fetch_pmn_accuracy_aggregate,
    fetch_product_health,
)
from ui.lib.components import paginator
from ui.lib.formatters import relative_time
from ui.lib.theme import COLORS, PLOTLY_LAYOUT, status_badge

st.markdown("# Health & Observability")
st.caption("System status, connector health, and PMN accuracy")

# ---------------------------------------------------------------------------
# System status banner
# ---------------------------------------------------------------------------
with st.spinner("Loading health data..."):
    overview = fetch_health_overview()

if overview:
    sys_status = overview.get("system_status", "red")
    if sys_status == "green":
        st.success("All systems operational")
    elif sys_status == "yellow":
        st.warning("System degraded")
    else:
        st.error("Issues detected")
else:
    st.warning("Could not reach backend — health data unavailable")

if st.button("Refresh", key="health_refresh"):
    fetch_health_overview.clear()
    fetch_ingestion_health.clear()
    fetch_product_health.clear()
    fetch_computation_status.clear()
    fetch_pmn_accuracy_aggregate.clear()
    fetch_audit_results.clear()
    fetch_ingestion_runs.clear()
    st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Connector health cards
# ---------------------------------------------------------------------------
st.subheader("Connector Health")
ingestion_health = fetch_ingestion_health()

if ingestion_health:
    cols = st.columns(len(ingestion_health))
    for i, conn in enumerate(ingestion_health):
        with cols[i]:
            source = conn.get("source", "?")
            conn_status = "gray"
            if overview and overview.get("connectors"):
                for c in overview["connectors"]:
                    if c["source"] == source:
                        conn_status = c.get("status", "gray")
                        break

            st.markdown(
                f"### {source.title()} &nbsp; {status_badge(conn_status)}",
                unsafe_allow_html=True,
            )

            sr24 = conn.get("success_rate_24h")
            sr7 = conn.get("success_rate_7d")
            st.metric(
                "Success Rate 24h",
                f"{sr24:.0%}" if sr24 is not None else "N/A",
            )
            st.metric(
                "Success Rate 7d",
                f"{sr7:.0%}" if sr7 is not None else "N/A",
            )

            avg_dur = conn.get("avg_duration_s")
            st.caption(f"Avg duration: {avg_dur:.1f}s" if avg_dur else "No duration data")
            st.caption(f"Persisted: {conn.get('total_listings_persisted', 0)}")
            st.caption(f"Missing price: {conn.get('missing_price_total', 0)}")
            st.caption(f"Rejected title: {conn.get('rejected_title_total', 0)}")
else:
    st.info("No connector health data available")

# ---------------------------------------------------------------------------
# Connector success timeline chart
# ---------------------------------------------------------------------------
timeline_data = fetch_ingestion_runs(page_size=100)
timeline_runs = timeline_data.get("runs", [])

if timeline_runs:
    status_color_map: dict[str, str] = {
        "success": COLORS["success"],
        "error": COLORS["danger"],
        "running": COLORS["warning"],
        "no_data": COLORS["muted"],
    }

    tl_df = pd.DataFrame(timeline_runs)
    required_cols = {"source", "status", "started_at", "duration_s"}
    if required_cols.issubset(tl_df.columns):
        tl_df["started_at"] = pd.to_datetime(tl_df["started_at"], errors="coerce", utc=True)
        tl_df["duration_s"] = pd.to_numeric(tl_df["duration_s"], errors="coerce").fillna(60)
        tl_df = tl_df.dropna(subset=["started_at"])

        if not tl_df.empty:
            sources = sorted(tl_df["source"].dropna().unique().tolist())

            fig_tl = go.Figure()
            for run_status, color in status_color_map.items():
                subset = tl_df[tl_df["status"] == run_status]
                if subset.empty:
                    continue
                for _, row in subset.iterrows():
                    y_pos = sources.index(row["source"]) if row["source"] in sources else 0
                    fig_tl.add_trace(
                        go.Scatter(
                            x=[row["started_at"]],
                            y=[y_pos],
                            mode="markers",
                            marker={
                                "color": color,
                                "size": 10,
                                "symbol": "square",
                            },
                            name=run_status,
                            showlegend=False,
                            hovertemplate=(
                                f"<b>{row['source']}</b><br>"
                                f"Status: {run_status}<br>"
                                f"Duration: {row['duration_s']:.0f}s<br>"
                                f"Started: {row['started_at']}<extra></extra>"
                            ),
                        )
                    )

            # One legend entry per status
            for run_status, color in status_color_map.items():
                fig_tl.add_trace(
                    go.Scatter(
                        x=[None],
                        y=[None],
                        mode="markers",
                        marker={"color": color, "size": 10, "symbol": "square"},
                        name=run_status.replace("_", " ").title(),
                        showlegend=True,
                    )
                )

            layout_tl = dict(PLOTLY_LAYOUT)
            layout_tl["yaxis"] = dict(
                PLOTLY_LAYOUT.get("yaxis", {}),
                tickvals=list(range(len(sources))),
                ticktext=sources,
                gridcolor=COLORS["border"],
            )
            layout_tl["xaxis"] = dict(
                PLOTLY_LAYOUT.get("xaxis", {}),
                title="Time",
            )
            layout_tl["title"] = "Ingestion Run Timeline"
            layout_tl["height"] = 220 + 60 * len(sources)
            fig_tl.update_layout(**layout_tl)

            with st.expander("Connector Success Timeline", expanded=True):
                st.plotly_chart(fig_tl, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Stale products
# ---------------------------------------------------------------------------
st.subheader("Product Staleness")
product_health = fetch_product_health()

if product_health:
    stale = [p for p in product_health if p.get("is_stale")]
    if stale:
        st.warning(f"{len(stale)} products have not been ingested in 24+ hours")
        for p in stale:
            hours = p.get("hours_since_ingestion")
            hours_str = f"{hours:.1f}h" if hours is not None else "unknown"
            name = p.get("name", "Unknown product")
            if hours is not None and hours > 48:
                color = COLORS["danger"]
            else:
                color = COLORS["warning"]
            muted = COLORS["text_secondary"]
            with st.container(border=True):
                st.markdown(
                    f'<span style="color:{color}; font-weight:600;">{name}</span>'
                    f'<span style="color:{muted}"> — last seen {hours_str} ago</span>',
                    unsafe_allow_html=True,
                )
    else:
        st.success("All products are up to date")
else:
    st.info("No product health data available")

st.divider()

# ---------------------------------------------------------------------------
# Recent ingestion runs
# ---------------------------------------------------------------------------
st.subheader("Recent Ingestion Runs")

rc1, rc2 = st.columns(2)
with rc1:
    run_source_filter = st.selectbox(
        "Source", ["All", "ebay", "leboncoin", "vinted"], key="health_run_source"
    )
with rc2:
    run_status_filter = st.selectbox(
        "Status",
        ["All", "success", "error", "running", "no_data"],
        key="health_run_status",
    )

# Use paginator to get the current offset, then derive 1-based page for the API.
# We render paginator after the data fetch but we need the offset first.
# Read current page from session state directly so we can fetch before rendering.
_current_run_page: int = st.session_state.get("health_runs_page", 0)
_run_page_size = 20
_run_api_page = _current_run_page + 1  # paginator is 0-based; API is 1-based

runs_data = fetch_ingestion_runs(
    source=run_source_filter if run_source_filter != "All" else None,
    status=run_status_filter if run_status_filter != "All" else None,
    page=_run_api_page,
    page_size=_run_page_size,
)

runs = runs_data.get("runs", [])
runs_total = runs_data.get("total", 0)

if runs:
    runs_df = pd.DataFrame(runs)
    display_cols = [
        "source",
        "status",
        "started_at",
        "duration_s",
        "listings_persisted",
        "error_message",
    ]
    display_cols = [c for c in display_cols if c in runs_df.columns]
    st.dataframe(
        runs_df[display_cols],
        hide_index=True,
        use_container_width=True,
    )

    paginator("health_runs_page", runs_total, _run_page_size)
else:
    st.info("No ingestion runs found")

st.divider()

# ---------------------------------------------------------------------------
# PMN & Computation status
# ---------------------------------------------------------------------------
st.subheader("PMN & Computation")

comp = fetch_computation_status()
if comp:
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Active Products", comp.get("total_active_products", 0))
    cc2.metric("With PMN", comp.get("products_with_pmn", 0))
    cc3.metric(
        "PMN Coverage",
        f"{comp.get('pmn_coverage_pct', 0):.0f}%",
    )
    cc4.metric(
        "Today's Metrics",
        comp.get("products_with_today_metrics", 0),
    )
    st.caption(f"Last PMN computation: {relative_time(comp.get('latest_pmn_computation'))}")

    # PMN coverage gauge
    pmn_pct = float(comp.get("pmn_coverage_pct", 0))
    fig_gauge = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=pmn_pct,
            number={"suffix": "%", "font": {"color": COLORS["text"], "size": 28}},
            title={"text": "PMN Coverage", "font": {"color": COLORS["text_secondary"], "size": 14}},
            gauge={
                "axis": {
                    "range": [0, 100],
                    "tickcolor": COLORS["text_secondary"],
                    "tickfont": {"color": COLORS["text_secondary"]},
                },
                "bar": {"color": COLORS["primary"]},
                "bgcolor": COLORS["surface"],
                "bordercolor": COLORS["border"],
                "steps": [
                    {"range": [0, 50], "color": COLORS["danger_muted"]},
                    {"range": [50, 80], "color": COLORS["warning_muted"]},
                    {"range": [80, 100], "color": COLORS["success_muted"]},
                ],
                "threshold": {
                    "line": {"color": COLORS["success"], "width": 2},
                    "thickness": 0.75,
                    "value": 80,
                },
            },
        )
    )
    gauge_layout = dict(PLOTLY_LAYOUT)
    gauge_layout["height"] = 260
    gauge_layout["margin"] = {"l": 30, "r": 30, "t": 40, "b": 20}
    fig_gauge.update_layout(**gauge_layout)
    st.plotly_chart(fig_gauge, use_container_width=True)
else:
    st.info("Computation status unavailable")

# PMN accuracy aggregate
pmn_acc = fetch_pmn_accuracy_aggregate()
if pmn_acc:
    st.markdown("#### PMN Accuracy")
    pa1, pa2, pa3 = st.columns(3)
    mae = pmn_acc.get("overall_mae")
    pa1.metric("Overall MAE", f"{mae:.2f}" if mae is not None else "N/A")
    hit = pmn_acc.get("overall_hit_rate")
    pa2.metric("Hit Rate", f"{hit:.0%}" if hit is not None else "N/A")
    pa3.metric("Matched Sales", pmn_acc.get("total_matched_sales", 0))

    worst = pmn_acc.get("worst_products", [])
    if worst:
        with st.expander("Worst products by MAE"):
            st.dataframe(
                pd.DataFrame(worst)[["name", "mae", "mean_pct_error", "hit_rate", "matched_count"]],
                hide_index=True,
                use_container_width=True,
            )

    best = pmn_acc.get("best_products", [])
    if best:
        with st.expander("Best products by hit rate"):
            st.dataframe(
                pd.DataFrame(best)[["name", "mae", "mean_pct_error", "hit_rate", "matched_count"]],
                hide_index=True,
                use_container_width=True,
            )

st.divider()

# ---------------------------------------------------------------------------
# Connector audit quality
# ---------------------------------------------------------------------------
st.subheader("Connector Audit Quality")
audit = fetch_audit_results()

if audit:
    st.caption(f"Period: last {audit.get('period_days', 7)} days")
    st.metric("Total Audited", audit.get("total_audited", 0))

    accuracy = audit.get("accuracy")
    if accuracy and isinstance(accuracy, dict):
        acc_df = pd.DataFrame([{"Field": k, "Accuracy": v} for k, v in accuracy.items()])
        st.dataframe(acc_df, hide_index=True, use_container_width=True)

    failures = audit.get("recent_failures", [])
    if failures:
        with st.expander(f"Recent failures ({len(failures)})"):
            fail_df = pd.DataFrame(failures)
            display_cols_audit = ["obs_id", "source", "accuracy", "audited_at", "notes"]
            display_cols_audit = [c for c in display_cols_audit if c in fail_df.columns]
            st.dataframe(fail_df[display_cols_audit], hide_index=True, use_container_width=True)
else:
    st.info("No audit results available")

"""Health & Observability: system status, connector health, ingestion runs."""

import pandas as pd
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
from ui.lib.formatters import relative_time, status_dot

st.header("Health & Observability")

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
            # Determine status from overview connectors
            conn_status = "gray"
            if overview and overview.get("connectors"):
                for c in overview["connectors"]:
                    if c["source"] == source:
                        conn_status = c.get("status", "gray")
                        break

            st.markdown(f"### {status_dot(conn_status)} {source.title()}")

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
        with st.expander("View stale products"):
            stale_df = pd.DataFrame(stale)
            stale_df["Hours Since"] = stale_df["hours_since_ingestion"].apply(
                lambda x: f"{x:.1f}h" if x is not None else "Never"
            )
            st.dataframe(
                stale_df[["name", "Hours Since"]],
                hide_index=True,
                use_container_width=True,
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

if "health_runs_page" not in st.session_state:
    st.session_state.health_runs_page = 1

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

runs_data = fetch_ingestion_runs(
    source=run_source_filter if run_source_filter != "All" else None,
    status=run_status_filter if run_status_filter != "All" else None,
    page=st.session_state.health_runs_page,
    page_size=20,
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
        height=400,
    )

    # Pagination
    total_pages = max(1, (runs_total + 19) // 20)
    current = st.session_state.health_runs_page
    rp1, rp2, rp3 = st.columns([1, 2, 1])
    with rp1:
        if st.button("Prev", disabled=current <= 1, key="runs_prev"):
            st.session_state.health_runs_page = current - 1
            fetch_ingestion_runs.clear()
            st.rerun()
    with rp2:
        st.caption(f"Page {current} of {total_pages} ({runs_total} total)")
    with rp3:
        if st.button("Next", disabled=current >= total_pages, key="runs_next"):
            st.session_state.health_runs_page = current + 1
            fetch_ingestion_runs.clear()
            st.rerun()
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
            display_cols = ["obs_id", "source", "accuracy", "audited_at", "notes"]
            display_cols = [c for c in display_cols if c in fail_df.columns]
            st.dataframe(fail_df[display_cols], hide_index=True, use_container_width=True)
else:
    st.info("No audit results available")

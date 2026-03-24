"""Import Data: trigger ingestion jobs and monitor status."""

from collections import defaultdict
from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

from ui.lib.api import (
    api_get,
    api_post,
    fetch_ingestion_runs,
    fetch_ingestion_status,
    fetch_products,
    fetch_queue_status,
)
from ui.lib.components import kpi_row
from ui.lib.config import SUPPORTED_PROVIDERS
from ui.lib.theme import COLORS, PLOTLY_LAYOUT

st.markdown("# Import Data")
st.caption("Trigger ingestion jobs and monitor queue status")

# ---------------------------------------------------------------------------
# Job trigger section
# ---------------------------------------------------------------------------
products = fetch_products()
if not products:
    st.warning("No products available. Configure them in Product Setup.")
else:
    col_main, col_status = st.columns([2, 1])

    with col_main:
        st.write("Select a configured product to launch ingestion jobs")

        product_map = {f"{prod['name']} ({prod['product_id'][:8]})": prod for prod in products}
        selected_label = st.selectbox("Product", list(product_map.keys()), key="import_product")
        selected_product = product_map[selected_label]

        provider_selection = st.multiselect(
            "Providers",
            options=SUPPORTED_PROVIDERS,
            default=selected_product.get("providers") or SUPPORTED_PROVIDERS,
        )

        import_limit = st.slider("Max Items", 10, 100, 50, key="import_limit")

        ca, cb, cc = st.columns(3)
        with ca:
            if st.button("Queue Listings", use_container_width=True):
                with st.spinner("Enqueuing..."):
                    try:
                        r = api_post(
                            "/ingestion/trigger",
                            params={
                                "product_id": selected_product["product_id"],
                                "sold_limit": 0,
                                "listings_limit": import_limit,
                                "sources": provider_selection,
                            },
                            timeout=20.0,
                        )
                        if r.status_code == 200:
                            resp = r.json()
                            job_id = resp.get("job_id")
                            msg = "Listings ingestion queued"
                            if job_id:
                                msg += f" (Job: {job_id[:8]})"
                            st.success(msg)
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"API error: {exc}")

        with cb:
            if st.button("Queue Sold", use_container_width=True):
                with st.spinner("Enqueuing..."):
                    try:
                        r = api_post(
                            "/ingestion/trigger",
                            params={
                                "product_id": selected_product["product_id"],
                                "sold_limit": import_limit,
                                "listings_limit": 0,
                                "sources": provider_selection,
                            },
                            timeout=20.0,
                        )
                        if r.status_code == 200:
                            resp = r.json()
                            job_id = resp.get("job_id")
                            msg = "Sold ingestion queued"
                            if job_id:
                                msg += f" (Job: {job_id[:8]})"
                            st.success(msg)
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"API error: {exc}")

        with cc:
            if st.button("Queue Full Run", type="primary", use_container_width=True):
                with st.spinner("Enqueuing..."):
                    try:
                        r = api_post(
                            "/ingestion/trigger",
                            params={
                                "product_id": selected_product["product_id"],
                                "sold_limit": import_limit,
                                "listings_limit": import_limit,
                                "sources": provider_selection,
                            },
                            timeout=20.0,
                        )
                        if r.status_code == 200:
                            resp = r.json()
                            job_id = resp.get("job_id")
                            msg = "Full ingestion queued"
                            if job_id:
                                msg += f" (Job: {job_id[:8]})"
                            st.success(msg)
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"API error: {exc}")

        # Job status check
        st.divider()
        st.subheader("Check Job Status")
        job_id_input = st.text_input("Job ID", placeholder="Enter job ID to check...")
        if job_id_input and st.button("Check Status"):
            with st.spinner("Checking..."):
                try:
                    r = api_get(f"/ingestion/jobs/{job_id_input}", timeout=10.0)
                    if r.status_code == 200:
                        st.json(r.json())
                    else:
                        st.error(f"Job not found: {r.text}")
                except Exception as exc:
                    st.error(f"Error: {exc}")

        # ---------------------------------------------------------------------------
        # Ingestion activity bar chart — last 14 days, stacked by source
        # ---------------------------------------------------------------------------
        st.divider()
        st.subheader("Ingestion Activity (last 14 days)")

        runs_data = fetch_ingestion_runs(page_size=100)
        runs = runs_data.get("runs", [])

        if runs:
            # Group by (date, source) and sum listings_persisted
            daily_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for run in runs:
                raw_date = run.get("started_at") or run.get("created_at") or ""
                if not raw_date:
                    continue
                try:
                    day = datetime.fromisoformat(raw_date[:10]).strftime("%Y-%m-%d")
                except ValueError:
                    continue
                source = run.get("source", "unknown")
                persisted = run.get("listings_persisted") or 0
                daily_source[day][source] += persisted

            # Collect all dates and sources present in data
            all_dates = sorted(daily_source.keys())[-14:]
            all_sources: list[str] = sorted(
                {s for day_sources in daily_source.values() for s in day_sources}
            )

            source_colors = {
                "ebay": COLORS["primary"],
                "leboncoin": COLORS["success"],
                "vinted": COLORS["cta"],
            }

            traces: list[go.Bar] = []
            for source in all_sources:
                y_values = [daily_source[day].get(source, 0) for day in all_dates]
                traces.append(
                    go.Bar(
                        name=source,
                        x=all_dates,
                        y=y_values,
                        marker_color=source_colors.get(source, COLORS["muted"]),
                    )
                )

            layout = {
                **PLOTLY_LAYOUT,
                "barmode": "stack",
                "xaxis": {**PLOTLY_LAYOUT.get("xaxis", {}), "title": "Date"},
                "yaxis": {**PLOTLY_LAYOUT.get("yaxis", {}), "title": "Listings Persisted"},
                "height": 280,
            }
            fig = go.Figure(data=traces, layout=layout)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No ingestion run data available to display.")

    # ---------------------------------------------------------------------------
    # Status column
    # ---------------------------------------------------------------------------
    with col_status:
        st.subheader("Status")
        status = fetch_ingestion_status()
        if status:
            kpi_row(
                [
                    {"label": "Total Products", "value": status.get("total_products", 0)},
                    {"label": "Total Observations", "value": status.get("total_observations", 0)},
                ]
            )
            kpi_row(
                [
                    {"label": "Active Listings", "value": status.get("active_listings", 0)},
                    {"label": "Sold Items", "value": status.get("sold_observations", 0)},
                ]
            )

            st.divider()
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("eBay", status.get("ebay_observations", 0))
            sc2.metric("LBC", status.get("leboncoin_observations", 0))
            sc3.metric("Vinted", status.get("vinted_observations", 0))
        else:
            st.error("Could not fetch status")

        # Queue status
        st.divider()
        st.subheader("Queue")
        queue = fetch_queue_status()
        if queue:
            st.metric("Queued Jobs", queue.get("queued_jobs", 0))
            st.caption(f"ARQ: {'Connected' if queue.get('arq_connected') else 'Disconnected'}")
        else:
            st.caption("Queue status unavailable")

        st.divider()
        if st.button("Refresh Status"):
            fetch_ingestion_status.clear()
            fetch_queue_status.clear()
            st.rerun()

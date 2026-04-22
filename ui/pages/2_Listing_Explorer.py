"""Listing Explorer: browse all ingested listings with full detail view."""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.lib.api import api_get, fetch_listing_detail, fetch_products
from ui.lib.components import empty_state, kpi_row, listing_header, paginator, photo_gallery
from ui.lib.formatters import format_roi, format_score_badge, format_spread
from ui.lib.theme import COLORS, PLOTLY_LAYOUT

st.markdown("# Listing Explorer")
st.caption("Browse and filter all ingested listings — select a row to view full details")

PAGE_SIZE = 100

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "selected_obs_id" not in st.session_state:
    st.session_state.selected_obs_id = None

# ---------------------------------------------------------------------------
# Filters — Row 1: Source, Status, Search, Sort
# ---------------------------------------------------------------------------

fc1, fc2, fc3, fc4 = st.columns(4)

with fc1:
    filter_source = st.selectbox(
        "Source", ["All", "ebay", "leboncoin", "vinted"], key="explorer_source"
    )
with fc2:
    filter_status = st.selectbox("Status", ["All", "Active", "Sold"], key="explorer_status")
with fc3:
    filter_search = st.text_input(
        "Search Title",
        placeholder="Search in listing titles...",
        key="explorer_search",
    )
with fc4:
    sort_options = [
        "Recent First",
        "Oldest First",
        "Price (Low to High)",
        "Price (High to Low)",
        "Best Spread",
        "Highest Confidence",
        "Best ROI",
    ]
    filter_sort = st.selectbox("Sort By", sort_options, key="explorer_sort")

# ---------------------------------------------------------------------------
# Filters — Row 2: Price range, Product filter
# ---------------------------------------------------------------------------

cp1, cp2 = st.columns(2)
with cp1:
    min_price_filter = st.number_input(
        "Min Price", min_value=0.0, value=0.0, step=10.0, key="explorer_min_price"
    )
with cp2:
    max_price_filter = st.number_input(
        "Max Price (0 = no limit)", min_value=0.0, value=0.0, step=10.0, key="explorer_max_price"
    )

products = fetch_products()
product_options = ["All Products"] + [f"{p['name']} ({p['brand'] or 'No brand'})" for p in products]
selected_product_filter = st.selectbox("Filter by Product", product_options, key="explorer_product")

# ---------------------------------------------------------------------------
# Filters — Row 3: Scoring filters + toggles
# ---------------------------------------------------------------------------

sf1, sf2, sf3, sf4, sf5 = st.columns([2, 2, 1, 1, 1])

with sf1:
    min_confidence = st.slider(
        "Min Confidence",
        min_value=0,
        max_value=100,
        value=0,
        step=5,
        key="explorer_min_confidence",
        help="Minimum risk-adjusted confidence score (0–100)",
    )
with sf2:
    min_spread = st.number_input(
        "Min Spread (€)",
        min_value=0.0,
        value=0.0,
        step=1.0,
        key="explorer_min_spread",
        help="Minimum arbitrage spread in euros",
    )
with sf3:
    has_score_filter = st.toggle("Has Score", value=False, key="explorer_has_score")
with sf4:
    llm_filter = st.toggle("LLM Validated", value=False, key="explorer_llm")
with sf5:
    show_all_columns = st.toggle("All Columns", value=False, key="explorer_all_cols")

# ---------------------------------------------------------------------------
# Build query params
# ---------------------------------------------------------------------------

sort_map: dict[str, tuple[str, str]] = {
    "Recent First": ("observed_at", "desc"),
    "Oldest First": ("observed_at", "asc"),
    "Price (Low to High)": ("price", "asc"),
    "Price (High to Low)": ("price", "desc"),
    "Best Spread": ("spread", "desc"),
    "Highest Confidence": ("confidence", "desc"),
    "Best ROI": ("roi", "desc"),
}

_current_page: int = st.session_state.get("explorer_page", 0)
offset = _current_page * PAGE_SIZE

params: dict = {"limit": PAGE_SIZE, "offset": offset}

if filter_source != "All":
    params["source"] = filter_source

if filter_status == "Active":
    params["is_sold"] = False
elif filter_status == "Sold":
    params["is_sold"] = True

if filter_search:
    params["search"] = filter_search

if min_price_filter > 0:
    params["min_price"] = min_price_filter
if max_price_filter > 0:
    params["max_price"] = max_price_filter

if selected_product_filter != "All Products":
    product_idx = product_options.index(selected_product_filter) - 1
    if 0 <= product_idx < len(products):
        params["product_id"] = products[product_idx]["product_id"]

if llm_filter:
    params["llm_validated"] = True

if min_confidence > 0:
    params["min_confidence"] = min_confidence

if min_spread > 0:
    params["min_spread"] = min_spread

if has_score_filter:
    params["has_score"] = True

params["sort_by"], params["sort_order"] = sort_map[filter_sort]

# ---------------------------------------------------------------------------
# Fetch listings
# ---------------------------------------------------------------------------

try:
    with st.spinner("Loading listings..."):
        r = api_get("/listings/explore", params=params)
        r.raise_for_status()
        data = r.json()

    listings = data.get("listings", [])
    total = data.get("total", 0)

    st.metric("Total Matching Listings", total)

    # -----------------------------------------------------------------------
    # Two-tab layout
    # -----------------------------------------------------------------------

    tab_browse, tab_detail = st.tabs(["Browse", "Listing Detail"])

    # ========================================================================
    # BROWSE TAB
    # ========================================================================

    with tab_browse:
        if not listings:
            empty_state(
                "No listings found",
                "No listings match your current filters. Try adjusting the criteria above.",
            )
        else:
            df = pd.DataFrame(listings)

            if not df.empty:
                df["LLM"] = df["llm_validated"].apply(lambda x: "Validated" if x else "---")

                default_cols = [
                    "product_name",
                    "title",
                    "price",
                    "source",
                    "is_sold",
                    "condition",
                    "arbitrage_spread_eur",
                    "risk_adjusted_confidence",
                    "observed_at",
                    "LLM",
                ]
                extra_cols = [
                    "net_roi_pct",
                    "urgency_score",
                    "url",
                    "location",
                    "product_brand",
                    "seller_rating",
                    "shipping_cost",
                ]

                cols_to_show = default_cols + (extra_cols if show_all_columns else [])
                cols_to_show = [c for c in cols_to_show if c in df.columns]

                display_df = df[cols_to_show].copy()
                display_df = display_df.rename(
                    columns={
                        "product_name": "Product",
                        "product_brand": "Brand",
                        "title": "Title",
                        "price": "Price",
                        "source": "Source",
                        "is_sold": "Status",
                        "condition": "Condition",
                        "arbitrage_spread_eur": "Spread",
                        "risk_adjusted_confidence": "Confidence",
                        "net_roi_pct": "ROI %",
                        "urgency_score": "Urgency",
                        "observed_at": "Observed",
                        "url": "URL",
                        "location": "Location",
                        "seller_rating": "Seller Rating",
                        "shipping_cost": "Shipping",
                    }
                )
                display_df["Status"] = display_df["Status"].apply(
                    lambda x: "Sold" if x else "Active"
                )

                col_config: dict = {
                    "Title": st.column_config.TextColumn("Title", width="large"),
                    "Price": st.column_config.NumberColumn("Price", format="€%.2f"),
                    "Source": st.column_config.TextColumn("Source", width="small"),
                }
                if "Spread" in display_df.columns:
                    col_config["Spread"] = st.column_config.NumberColumn("Spread", format="€%.2f")
                if "Confidence" in display_df.columns:
                    col_config["Confidence"] = st.column_config.ProgressColumn(
                        "Confidence", min_value=0, max_value=100, format="%.0f"
                    )
                if "ROI %" in display_df.columns:
                    col_config["ROI %"] = st.column_config.NumberColumn("ROI %", format="%.1f%%")
                if "Urgency" in display_df.columns:
                    col_config["Urgency"] = st.column_config.ProgressColumn(
                        "Urgency", min_value=0, max_value=1, format="%.2f"
                    )
                if "URL" in display_df.columns:
                    col_config["URL"] = st.column_config.LinkColumn(
                        "Link", width="small", display_text="View"
                    )

                event = st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config=col_config,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="explorer_table",
                )

                # Handle row selection — persist obs_id across tab switches
                sel_idx: int | None = None
                if event and event.selection and event.selection.rows:
                    sel_idx = event.selection.rows[0]
                    if sel_idx < len(df):
                        obs_id_col = "observation_id" if "observation_id" in df.columns else "id"
                        if obs_id_col in df.columns:
                            st.session_state.selected_obs_id = df.iloc[sel_idx][obs_id_col]
                        elif "obs_id" in df.columns:
                            st.session_state.selected_obs_id = df.iloc[sel_idx]["obs_id"]

                # Action area when a row is selected
                if st.session_state.selected_obs_id is not None and sel_idx is not None:
                    ac1, ac2 = st.columns(2)
                    with ac1:
                        st.info(
                            "Switch to the **Listing Detail** tab above to see full details "
                            "for the selected listing."
                        )
                    with ac2:
                        product_id_col = "product_id" if "product_id" in df.columns else None
                        if product_id_col and sel_idx < len(df):
                            if st.button("View in Discovery", key="goto_discovery"):
                                st.session_state.selected_product_id = df.iloc[sel_idx][
                                    product_id_col
                                ]
                                st.switch_page("pages/1_Discovery.py")

                # Pagination
                paginator("explorer_page", total, PAGE_SIZE)

                # CSV export
                st.download_button(
                    label="Download as CSV",
                    data=df.to_csv(index=False).encode("utf-8"),
                    file_name=f"listings_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                )

                # -------------------------------------------------------------------
                # Price distribution chart
                # -------------------------------------------------------------------
                with st.expander("Price Distribution"):
                    prices = pd.to_numeric(df.get("price", pd.Series(dtype=float)), errors="coerce")
                    prices = prices.dropna()
                    if not prices.empty:
                        fig_hist = go.Figure(
                            go.Histogram(
                                x=prices,
                                nbinsx=30,
                                marker_color=COLORS["primary"],
                                opacity=0.85,
                                hovertemplate="Price: %{x:.2f}<br>Count: %{y}<extra></extra>",
                            )
                        )
                        hist_layout = dict(PLOTLY_LAYOUT)
                        hist_layout["xaxis"] = dict(PLOTLY_LAYOUT.get("xaxis", {}), title="Price")
                        hist_layout["yaxis"] = dict(
                            PLOTLY_LAYOUT.get("yaxis", {}), title="Listings"
                        )
                        hist_layout["title"] = f"Price Distribution — {len(prices)} listings"
                        fig_hist.update_layout(**hist_layout)
                        st.plotly_chart(fig_hist, use_container_width=True)
                    else:
                        st.info("No price data available for the current page.")

                # -------------------------------------------------------------------
                # Statistics + source donut
                # -------------------------------------------------------------------
                with st.expander("Statistics"):
                    stat_cols, donut_col = st.columns([3, 2])

                    with stat_cols:
                        s1, s2, s3, s4 = st.columns(4)
                        with s1:
                            avg = df["price"].mean() if "price" in df.columns else None
                            st.metric("Average Price", f"€{avg:.2f}" if pd.notna(avg) else "N/A")
                        with s2:
                            med = df["price"].median() if "price" in df.columns else None
                            st.metric("Median Price", f"€{med:.2f}" if pd.notna(med) else "N/A")
                        with s3:
                            st.metric(
                                "Sold Items",
                                int(df["is_sold"].sum()) if "is_sold" in df.columns else 0,
                            )
                        with s4:
                            st.metric(
                                "Active Listings",
                                int((~df["is_sold"]).sum()) if "is_sold" in df.columns else 0,
                            )

                    with donut_col:
                        if "source" in df.columns:
                            source_counts = df["source"].value_counts()
                            source_colors = [
                                COLORS["primary"],
                                COLORS["success"],
                                COLORS["warning"],
                                COLORS["danger"],
                                COLORS["muted"],
                            ]
                            fig_donut = go.Figure(
                                go.Pie(
                                    labels=source_counts.index.tolist(),
                                    values=source_counts.values.tolist(),
                                    hole=0.55,
                                    marker={"colors": source_colors[: len(source_counts)]},
                                    textinfo="label+percent",
                                    hovertemplate="%{label}: %{value}<extra></extra>",
                                    textfont={"color": COLORS["text"], "size": 11},
                                )
                            )
                            donut_layout = dict(PLOTLY_LAYOUT)
                            donut_layout["title"] = "By Source"
                            donut_layout["height"] = 240
                            donut_layout["margin"] = {"l": 10, "r": 10, "t": 40, "b": 10}
                            donut_layout["showlegend"] = False
                            fig_donut.update_layout(**donut_layout)
                            st.plotly_chart(fig_donut, use_container_width=True)

    # ========================================================================
    # LISTING DETAIL TAB
    # ========================================================================

    with tab_detail:
        obs_id = st.session_state.selected_obs_id

        if not obs_id:
            empty_state(
                "No listing selected",
                "Select a row in the Browse tab to view detailed listing information.",
            )
        else:
            with st.spinner("Loading listing details..."):
                detail_data = fetch_listing_detail(obs_id)

            if not detail_data:
                st.error("Failed to load listing details.")
            else:
                obs = detail_data.get("observation", {})
                detail = detail_data.get("detail")
                enrichment = detail_data.get("enrichment")
                score = detail_data.get("score")
                pmn_data = detail_data.get("pmn")

                # ---- HEADER ----
                listing_header(obs, score)

                st.divider()

                # ---- PRICING & ARBITRAGE ----
                st.markdown("#### Pricing & Arbitrage")
                if score:
                    kpi_row(
                        [
                            {
                                "label": "Price",
                                "value": f"€{obs.get('price', 0):.2f}",
                            },
                            {
                                "label": "PMN",
                                "value": (
                                    f"€{pmn_data['pmn']:.2f}"
                                    if pmn_data and pmn_data.get("pmn")
                                    else "N/A"
                                ),
                            },
                            {
                                "label": "Spread",
                                "value": format_spread(score.get("arbitrage_spread_eur")),
                            },
                            {
                                "label": "ROI",
                                "value": format_roi(score.get("net_roi_pct")),
                            },
                            {
                                "label": "Confidence",
                                "value": format_score_badge(score.get("risk_adjusted_confidence")),
                            },
                        ]
                    )

                    # Cost breakdown from score_breakdown
                    breakdown = score.get("score_breakdown")
                    if breakdown:
                        with st.expander("Cost Breakdown"):
                            acq = breakdown.get("acquisition_cost", {})
                            st.markdown(f"""
| Component | Value |
|-----------|-------|
| Purchase price | €{acq.get("price", "N/A")} |
| Shipping (buy) | €{acq.get("shipping", "N/A")} |
| Buyer fee | {acq.get("buyer_fee", "N/A")} |
| **Acquisition cost** | **€{score.get("acquisition_cost_eur", "N/A")}** |
| Estimated sale price | €{score.get("estimated_sale_price_eur", "N/A")} |
| Sell fees | -€{score.get("estimated_sell_fees_eur", "N/A")} |
| Sell shipping | -€{score.get("estimated_sell_shipping_eur", "N/A")} |
| **Net spread** | **€{score.get("arbitrage_spread_eur", "N/A")}** |
""")
                else:
                    st.info(
                        "This listing has not been scored yet. "
                        "Scoring runs hourly after enrichment."
                    )

                st.divider()

                # ---- PHOTOS & DESCRIPTION ----
                st.markdown("#### Listing Details")
                if detail:
                    photo_gallery(detail.get("photo_urls"))

                    desc = detail.get("description")
                    if desc:
                        st.text_area("Description", value=desc, height=150, disabled=True)

                    meta_cols = st.columns(5)
                    with meta_cols[0]:
                        posted = detail.get("original_posted_at")
                        st.metric("Posted", posted[:10] if posted else "---")
                    with meta_cols[1]:
                        st.metric("Views", detail.get("view_count") or "---")
                    with meta_cols[2]:
                        st.metric("Favorites", detail.get("favorite_count") or "---")
                    with meta_cols[3]:
                        nego = detail.get("negotiation_enabled")
                        st.metric(
                            "Negotiable",
                            "Yes" if nego else ("No" if nego is False else "---"),
                        )
                    with meta_cols[4]:
                        pickup = detail.get("local_pickup_only")
                        st.metric(
                            "Pickup Only",
                            "Yes" if pickup else ("No" if pickup is False else "---"),
                        )
                else:
                    st.info("Detail data not yet fetched for this listing.")

                st.divider()

                # ---- SELLER PROFILE ----
                st.markdown("#### Seller Profile")
                seller_metrics: list[dict] = [
                    {
                        "label": "Rating",
                        "value": (
                            f"{obs.get('seller_rating', 0):.1f}"
                            if obs.get("seller_rating")
                            else "---"
                        ),
                    },
                ]
                if detail:
                    age = detail.get("seller_account_age_days")
                    seller_metrics.append(
                        {
                            "label": "Account Age",
                            "value": f"{age} days" if age else "---",
                        }
                    )
                    txn = detail.get("seller_transaction_count")
                    seller_metrics.append(
                        {"label": "Transactions", "value": str(txn) if txn else "---"}
                    )
                if enrichment:
                    motiv = enrichment.get("seller_motivation_score")
                    seller_metrics.append(
                        {
                            "label": "Motivation",
                            "value": f"{float(motiv):.0%}" if motiv is not None else "---",
                        }
                    )
                kpi_row(seller_metrics)

                st.divider()

                # ---- LLM ENRICHMENT ----
                st.markdown("#### LLM Enrichment")
                if enrichment:
                    e_cols = st.columns(4)
                    with e_cols[0]:
                        urg = enrichment.get("urgency_score")
                        st.metric("Urgency", f"{float(urg):.0%}" if urg is not None else "---")
                    with e_cols[1]:
                        qual = enrichment.get("listing_quality_score")
                        st.metric("Quality", f"{float(qual):.0%}" if qual is not None else "---")
                    with e_cols[2]:
                        cond = enrichment.get("condition_confidence")
                        st.metric(
                            "Condition Trust",
                            f"{float(cond):.0%}" if cond is not None else "---",
                        )
                    with e_cols[3]:
                        fake = enrichment.get("fakeness_probability")
                        st.metric(
                            "Fake Risk",
                            f"{float(fake):.0%}" if fake is not None else "---",
                        )

                    keywords = enrichment.get("urgency_keywords", [])
                    if keywords:
                        st.markdown(
                            "**Urgency keywords:** " + ", ".join(f"`{k}`" for k in keywords)
                        )

                    accessories = enrichment.get("accessories_included", [])
                    if accessories:
                        st.markdown("**Accessories:** " + ", ".join(accessories))

                    bool_parts = []
                    if enrichment.get("has_original_box"):
                        bool_parts.append("Has original box")
                    if enrichment.get("has_receipt_or_invoice"):
                        bool_parts.append("Has receipt/invoice")
                    if bool_parts:
                        st.markdown("  |  ".join(f"✓ {p}" for p in bool_parts))
                else:
                    st.info("This listing has not been enriched yet. Enrichment runs hourly.")

                # ---- SCORE BREAKDOWN ----
                if score and score.get("score_breakdown"):
                    st.divider()
                    st.markdown("#### Score Breakdown")
                    confidence_factors = score["score_breakdown"].get("confidence_factors", {})
                    if confidence_factors:
                        factor_df = pd.DataFrame(
                            [
                                {
                                    "Factor": k.replace("_", " ").title(),
                                    "Value": f"{v:.3f}" if v is not None else "---",
                                }
                                for k, v in confidence_factors.items()
                            ]
                        )
                        st.dataframe(factor_df, use_container_width=True, hide_index=True)

except Exception as exc:
    st.error(f"Failed to load listings: {exc}")

"""Discovery page: find arbitrage opportunities."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.lib.api import (
    fetch_analytics,
    fetch_category_names,
    fetch_discovery,
    fetch_pmn_accuracy,
    fetch_price_history,
    fetch_product_detail,
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "selected_product_id" not in st.session_state:
    st.session_state.selected_product_id = None
if "discovery_page" not in st.session_state:
    st.session_state.discovery_page = 0

PAGE_SIZE = 50

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")

    # Quick presets
    st.subheader("Quick Presets")
    preset = st.radio(
        "Select Preset",
        ["High Margin", "Fast Movers", "Low Risk", "Trending", "Custom"],
        index=4,
        key="preset_select",
    )

    if preset == "High Margin":
        default_discount, default_liq, default_trend = 20, None, None
    elif preset == "Fast Movers":
        default_discount, default_liq, default_trend = 0, 0.8, None
    elif preset == "Low Risk":
        default_discount, default_liq, default_trend = 0, 0.6, None
    elif preset == "Trending":
        default_discount, default_liq, default_trend = 0, None, 0.5
    else:
        default_discount, default_liq, default_trend = 0, None, None

    st.divider()
    st.subheader("Advanced Filters")

    search_query = st.text_input("Search", placeholder="Product name or brand...", key="search")
    categories = fetch_category_names()
    selected_category = st.selectbox("Category", ["All"] + categories, key="category_select")
    brand_filter = st.text_input("Brand", placeholder="e.g., Sony", key="brand_filter")

    # Discount filter (positive = good deal)
    discount_min = st.slider(
        "Minimum Discount %",
        min_value=0,
        max_value=50,
        value=default_discount or 0,
        step=5,
        key="discount_slider",
        help="Higher = better deal (% below PMN)",
    )

    liquidity_filter = st.slider(
        "Min Liquidity",
        0.0,
        1.0,
        default_liq or 0.0,
        0.1,
        key="liquidity",
    )
    trend_filter = st.slider("Min Trend", -1.0, 2.0, default_trend or -1.0, 0.1, key="trend")

    st.divider()
    sort_by = st.selectbox(
        "Sort By",
        ["margin", "liquidity", "trend"],
        format_func=lambda x: {
            "margin": "Best Margin",
            "liquidity": "Highest Liquidity",
            "trend": "Trending",
        }[x],
        key="sort_select",
    )

    st.divider()
    if st.button("Refresh", use_container_width=True):
        fetch_discovery.clear()
        fetch_analytics.clear()
        st.session_state.discovery_page = 0
        st.rerun()

# ---------------------------------------------------------------------------
# Analytics overview
# ---------------------------------------------------------------------------
with st.spinner("Loading analytics..."):
    analytics = fetch_analytics()

if analytics:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Opportunities", analytics.get("opportunities_count", 0))
    c2.metric("Products", analytics.get("total_products", 0))
    c3.metric("Active Listings", analytics.get("active_listings", 0))
    c4.metric("Sold Items", analytics.get("sold_items", 0))
    c5.metric("Recent (24h)", analytics.get("recent_observations_24h", 0))
    st.divider()

# ---------------------------------------------------------------------------
# Discovery table
# ---------------------------------------------------------------------------
col_left, col_right = st.columns([3, 2])

with col_left:
    st.subheader("Discovery Opportunities")

    category_param = "" if selected_category == "All" else selected_category
    min_margin_param = -discount_min if discount_min > 0 else None
    liquidity_param = liquidity_filter if liquidity_filter > 0 else None
    trend_param = trend_filter if trend_filter > -1.0 else None

    current_page = st.session_state.discovery_page
    offset = current_page * PAGE_SIZE

    with st.spinner("Loading opportunities..."):
        discovery_data = fetch_discovery(
            category=category_param,
            brand=brand_filter or "",
            min_margin=min_margin_param,
            min_liquidity=liquidity_param,
            min_trend=trend_param,
            sort_by=sort_by,
            limit=PAGE_SIZE,
            offset=offset,
        )

    items = discovery_data.get("items", [])
    total = discovery_data.get("total", 0)

    if not items:
        st.info("No opportunities found. Try adjusting your filters or ingest data first.")
        st.markdown(
            """
            **Next Steps:**
            1. Go to **Product Setup** to configure products
            2. Use **Import Data** to trigger scraping
            3. Return here and click Refresh
            """
        )
    else:
        df = pd.DataFrame(items)

        # Search filter (client-side)
        if search_query:
            df = df[df["title"].str.contains(search_query, case=False, na=False)]

        # Add computed columns
        df["Discount %"] = df["delta_vs_pmn_pct"].apply(
            lambda x: round(-x, 1) if x is not None else None
        )
        df["Confidence"] = df.get("pmn_confidence")

        # Rename for display
        display_df = df.rename(
            columns={
                "title": "Product",
                "brand": "Brand",
                "pmn": "PMN",
                "price_min_market": "Best Price",
                "liquidity_score": "Liquidity",
                "trend_score": "Trend",
            }
        )

        cols_to_show = [
            "Product",
            "PMN",
            "Best Price",
            "Discount %",
            "Confidence",
            "Liquidity",
            "Trend",
        ]
        cols_to_show = [c for c in cols_to_show if c in display_df.columns]

        event = st.dataframe(
            display_df[cols_to_show],
            use_container_width=True,
            hide_index=True,
            height=500,
            column_config={
                "Product": st.column_config.TextColumn("Product", width="large"),
                "PMN": st.column_config.NumberColumn("PMN", format="%.0f"),
                "Best Price": st.column_config.NumberColumn("Best Price", format="%.0f"),
                "Discount %": st.column_config.NumberColumn("Discount %", format="%.1f%%"),
                "Confidence": st.column_config.ProgressColumn(
                    "Confidence", min_value=0, max_value=1, format="%.0f%%"
                ),
                "Liquidity": st.column_config.NumberColumn("Liquidity", format="%.2f"),
                "Trend": st.column_config.NumberColumn("Trend", format="%.2f"),
            },
            on_select="rerun",
            selection_mode="single-row",
            key="discovery_table",
        )

        # Handle row selection
        if event and event.selection and event.selection.rows:
            selected_idx = event.selection.rows[0]
            if selected_idx < len(df):
                st.session_state.selected_product_id = df.iloc[selected_idx]["product_id"]

        # Pagination
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        start_item = offset + 1
        end_item = min(offset + len(items), total)
        st.caption(f"Showing {start_item}-{end_item} of {total}")

        p1, p2, p3 = st.columns([1, 2, 1])
        with p1:
            if st.button("Previous", disabled=current_page == 0):
                st.session_state.discovery_page = current_page - 1
                st.rerun()
        with p2:
            st.markdown(
                f"<center>Page {current_page + 1} of {total_pages}</center>",
                unsafe_allow_html=True,
            )
        with p3:
            if st.button("Next", disabled=current_page >= total_pages - 1):
                st.session_state.discovery_page = current_page + 1
                st.rerun()

# ---------------------------------------------------------------------------
# Product detail panel
# ---------------------------------------------------------------------------
with col_right:
    st.subheader("Product Detail")

    product_id = st.session_state.selected_product_id
    if not product_id:
        st.info("Select a product from the table to see details")
    else:
        with st.spinner("Loading details..."):
            product = fetch_product_detail(product_id)

        if not product:
            st.error("Failed to load product details")
        else:
            with st.container(height=700):
                st.markdown(f"### {product['title']}")
                if product.get("brand"):
                    st.caption(f"Brand: {product['brand']}")
                if product.get("category"):
                    st.caption(f"Category: {product['category']}")

                st.divider()

                # Market summary
                st.markdown("#### Market Summary")
                m1, m2 = st.columns(2)
                with m1:
                    pmn_val = product.get("pmn")
                    if pmn_val:
                        st.metric("PMN (Ref)", f"{pmn_val:.2f}")
                        st.caption(
                            f"Range: {product.get('pmn_low', 0):.2f}"
                            f" - {product.get('pmn_high', 0):.2f}"
                        )
                    else:
                        st.metric("PMN (Ref)", "N/A")
                with m2:
                    med = product.get("price_median_30d")
                    st.metric("Median 30d", f"{med:.2f}" if med else "N/A")

                m3, m4 = st.columns(2)
                with m3:
                    liq = product.get("liquidity_score")
                    st.metric("Liquidity", f"{liq:.2f}" if liq is not None else "N/A")
                with m4:
                    trend = product.get("trend_score")
                    st.metric("Trend", f"{trend:.2f}" if trend is not None else "N/A")

                # PMN confidence section
                st.divider()
                st.markdown("#### PMN Confidence")
                pmn_acc = fetch_pmn_accuracy(product_id)
                if pmn_acc:
                    ac1, ac2 = st.columns(2)
                    with ac1:
                        hit = pmn_acc.get("overall_hit_rate")
                        st.metric(
                            "Hit Rate",
                            f"{hit:.0%}" if hit is not None else "N/A",
                        )
                    with ac2:
                        mae = pmn_acc.get("overall_mae")
                        st.metric(
                            "MAE",
                            f"{mae:.2f}" if mae is not None else "N/A",
                        )
                    st.caption(f"Based on {pmn_acc.get('matched_count', 0)} matched sales")
                else:
                    st.info("No PMN accuracy data yet")

                # Price history chart
                st.divider()
                st.markdown("#### Price History")
                price_history = fetch_price_history(product_id, days=30)

                if price_history and (
                    price_history.get("sold_history") or price_history.get("active_history")
                ):
                    fig = go.Figure()
                    sold_hist = price_history.get("sold_history", [])
                    if sold_hist:
                        fig.add_trace(
                            go.Scatter(
                                x=[h["date"] for h in sold_hist],
                                y=[h["avg_price"] for h in sold_hist],
                                mode="markers+lines",
                                name="Sold Avg",
                                line=dict(color="green"),
                            )
                        )
                    active_hist = price_history.get("active_history", [])
                    if active_hist:
                        fig.add_trace(
                            go.Scatter(
                                x=[h["date"] for h in active_hist],
                                y=[h["avg_price"] for h in active_hist],
                                mode="markers+lines",
                                name="Active Avg",
                                line=dict(color="blue", dash="dash"),
                            )
                        )
                    pmn = price_history.get("pmn")
                    if pmn:
                        fig.add_hline(
                            y=pmn,
                            line_dash="dot",
                            line_color="red",
                            annotation_text="PMN",
                        )
                    fig.update_layout(
                        xaxis_title="Date",
                        yaxis_title="Price",
                        height=300,
                        margin=dict(l=0, r=0, t=20, b=0),
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No price history available yet")

                # Recent sold
                st.divider()
                st.markdown("#### Recent Sold (Last 30d)")
                recent_solds = product.get("recent_solds", [])
                if recent_solds:
                    sold_df = pd.DataFrame(recent_solds[:5])
                    sold_df["price_display"] = sold_df["price"].apply(
                        lambda x: f"{x:.2f}" if x else "N/A"
                    )
                    sold_df["date"] = pd.to_datetime(sold_df["observed_at"]).dt.strftime("%Y-%m-%d")
                    st.dataframe(
                        sold_df[["date", "price_display", "condition", "source"]],
                        hide_index=True,
                        use_container_width=True,
                    )
                else:
                    st.info("No sold items found")

                # Live listings
                st.divider()
                st.markdown("#### Live Listings")
                live_listings = product.get("live_listings", [])
                if live_listings:
                    live_df = pd.DataFrame(live_listings[:5])
                    live_df["price_display"] = live_df["price"].apply(
                        lambda x: f"{x:.2f}" if x else "N/A"
                    )
                    st.dataframe(
                        live_df[["price_display", "condition", "location", "source"]],
                        hide_index=True,
                        use_container_width=True,
                    )
                else:
                    st.info("No active listings found")

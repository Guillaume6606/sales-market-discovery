"""Discovery page: browse and analyse arbitrage opportunities."""

from typing import Any

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
from ui.lib.components import empty_state, kpi_row, paginator
from ui.lib.theme import COLORS, PLOTLY_LAYOUT

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAGE_SIZE = 50

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "selected_product_id" not in st.session_state:
    st.session_state.selected_product_id = None
if "discovery_page" not in st.session_state:
    st.session_state.discovery_page = 0

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Filters")

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
        "Min Liquidity", 0.0, 1.0, default_liq or 0.0, 0.1, key="liquidity"
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
# Page header
# ---------------------------------------------------------------------------

st.markdown("# Discovery")
st.caption("Browse arbitrage opportunities ranked by margin, liquidity, and trend.")

# ---------------------------------------------------------------------------
# Analytics KPI row
# ---------------------------------------------------------------------------

with st.spinner("Loading analytics..."):
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
    st.divider()

# ---------------------------------------------------------------------------
# Fetch discovery data (needed by both tabs)
# ---------------------------------------------------------------------------

category_param = "" if selected_category == "All" else selected_category
min_margin_param = -discount_min if discount_min > 0 else None
liquidity_param = liquidity_filter if liquidity_filter > 0 else None
trend_param = trend_filter if trend_filter > -1.0 else None

offset = st.session_state.discovery_page * PAGE_SIZE

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

items: list[dict[str, Any]] = discovery_data.get("items", [])
total: int = discovery_data.get("total", 0)

# ---------------------------------------------------------------------------
# Build display DataFrame once (shared by scatter + table)
# ---------------------------------------------------------------------------

df: pd.DataFrame = pd.DataFrame()
display_df: pd.DataFrame = pd.DataFrame()

if items:
    df = pd.DataFrame(items)

    if search_query:
        df = df[df["title"].str.contains(search_query, case=False, na=False)]

    df["Discount %"] = df["delta_vs_pmn_pct"].apply(
        lambda x: round(-x, 1) if x is not None else None
    )
    df["Confidence"] = df.get("pmn_confidence")

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

# ---------------------------------------------------------------------------
# Main tabs
# ---------------------------------------------------------------------------

tab_opportunities, tab_detail = st.tabs(["Opportunities", "Product Detail"])

# ============================================================
# Tab 1 — Opportunities
# ============================================================

with tab_opportunities:
    if not items:
        empty_state(
            "No opportunities found",
            "Try adjusting your filters, or ingest data first.\n\n"
            "**Next Steps:**\n"
            "1. Go to **Product Setup** to configure products\n"
            "2. Use **Import Data** to trigger scraping\n"
            "3. Return here and click Refresh",
        )
    else:
        # ---- Scatter overview ----
        with st.expander("Opportunity Overview (scatter)", expanded=False):
            _color_col = "category" if "category" in df.columns else "Brand"
            _x = (
                df["liquidity_score"] if "liquidity_score" in df.columns else pd.Series(dtype=float)
            )
            _y = df["Discount %"] if "Discount %" in df.columns else pd.Series(dtype=float)
            _size_raw = (
                df["trend_score"].abs() if "trend_score" in df.columns else pd.Series(dtype=float)
            )
            # Normalise bubble size to [8, 40] to avoid invisible/huge dots
            _size_min, _size_max = _size_raw.min(), _size_raw.max()
            if pd.notna(_size_min) and pd.notna(_size_max) and _size_max > _size_min:
                _size = 8 + (_size_raw - _size_min) / (_size_max - _size_min) * 32
            else:
                _size = pd.Series([14.0] * len(df))

            _color_values = df[_color_col] if _color_col in df.columns else pd.Series(dtype=str)

            scatter_fig = go.Figure(
                go.Scatter(
                    x=_x,
                    y=_y,
                    mode="markers",
                    marker=dict(
                        size=_size.fillna(14).tolist(),
                        color=_color_values.astype("category").cat.codes
                        if len(_color_values)
                        else [],
                        colorscale="Viridis",
                        showscale=False,
                        opacity=0.8,
                        line=dict(color=COLORS["border"], width=1),
                    ),
                    text=df["title"] if "title" in df.columns else None,
                    customdata=_color_values.tolist() if len(_color_values) else None,
                    hovertemplate=(
                        "<b>%{text}</b><br>"
                        f"{_color_col}: %{{customdata}}<br>"
                        "Liquidity: %{x:.2f}<br>"
                        "Discount: %{y:.1f}%<extra></extra>"
                    ),
                )
            )
            scatter_layout = {**PLOTLY_LAYOUT}
            scatter_layout["xaxis"] = {
                **scatter_layout.get("xaxis", {}),
                "title": "Liquidity Score",
            }
            scatter_layout["yaxis"] = {
                **scatter_layout.get("yaxis", {}),
                "title": "Discount % (vs PMN)",
            }
            scatter_layout["height"] = 320
            scatter_layout["title"] = {
                "text": f"Coloured by {_color_col} — bubble size = |trend score|",
                "font": {"size": 12, "color": COLORS["text_secondary"]},
            }
            scatter_fig.update_layout(**scatter_layout)
            st.plotly_chart(scatter_fig, use_container_width=True)

        # ---- Opportunity table ----
        cols_to_show = [
            c
            for c in [
                "Product",
                "PMN",
                "Best Price",
                "Discount %",
                "Confidence",
                "Liquidity",
                "Trend",
            ]
            if c in display_df.columns
        ]

        event = st.dataframe(
            display_df[cols_to_show],
            use_container_width=True,
            hide_index=True,
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

        # Handle row selection — persists selected product across tab switches
        if event and event.selection and event.selection.rows:
            selected_idx = event.selection.rows[0]
            if selected_idx < len(df):
                st.session_state.selected_product_id = df.iloc[selected_idx]["product_id"]

        # ---- Pagination ----
        offset = paginator("discovery_page", total, PAGE_SIZE)

        # ---- CSV export ----
        csv_bytes = display_df[cols_to_show].to_csv(index=False).encode()
        st.download_button(
            label="Export to CSV",
            data=csv_bytes,
            file_name="discovery_opportunities.csv",
            mime="text/csv",
            use_container_width=False,
        )

# ============================================================
# Tab 2 — Product Detail
# ============================================================

with tab_detail:
    product_id: str | None = st.session_state.selected_product_id

    if not product_id:
        empty_state(
            "No product selected",
            "Select a row in the Opportunities tab to load detailed market data.",
        )
    else:
        with st.spinner("Loading details..."):
            product = fetch_product_detail(product_id)

        if not product:
            st.error("Failed to load product details.")
        else:
            st.markdown(f"### {product['title']}")
            meta_parts: list[str] = []
            if product.get("brand"):
                meta_parts.append(f"Brand: {product['brand']}")
            if product.get("category"):
                meta_parts.append(f"Category: {product['category']}")
            if meta_parts:
                st.caption("  |  ".join(meta_parts))

            st.divider()

            # ---- Market summary ----
            st.markdown("#### Market Summary")
            m1, m2 = st.columns(2)
            with m1:
                pmn_val = product.get("pmn")
                if pmn_val:
                    st.metric("PMN (Ref)", f"{pmn_val:.2f}")
                    st.caption(
                        f"Range: {product.get('pmn_low', 0):.2f} - {product.get('pmn_high', 0):.2f}"
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

            # ---- PMN confidence ----
            st.divider()
            st.markdown("#### PMN Confidence")
            pmn_acc = fetch_pmn_accuracy(product_id)
            if pmn_acc:
                ac1, ac2 = st.columns(2)
                with ac1:
                    hit = pmn_acc.get("overall_hit_rate")
                    st.metric("Hit Rate", f"{hit:.0%}" if hit is not None else "N/A")
                with ac2:
                    mae = pmn_acc.get("overall_mae")
                    st.metric("MAE", f"{mae:.2f}" if mae is not None else "N/A")
                st.caption(f"Based on {pmn_acc.get('matched_count', 0)} matched sales")
            else:
                st.info("No PMN accuracy data yet.")

            # ---- Price history chart ----
            st.divider()
            st.markdown("#### Price History")
            price_history = fetch_price_history(product_id, days=30)

            if price_history and (
                price_history.get("sold_history") or price_history.get("active_history")
            ):
                fig = go.Figure()

                sold_hist: list[dict[str, Any]] = price_history.get("sold_history", [])
                if sold_hist:
                    fig.add_trace(
                        go.Scatter(
                            x=[h["date"] for h in sold_hist],
                            y=[h["avg_price"] for h in sold_hist],
                            mode="markers+lines",
                            name="Sold Avg",
                            line=dict(color=COLORS["success"]),
                        )
                    )

                active_hist: list[dict[str, Any]] = price_history.get("active_history", [])
                if active_hist:
                    fig.add_trace(
                        go.Scatter(
                            x=[h["date"] for h in active_hist],
                            y=[h["avg_price"] for h in active_hist],
                            mode="markers+lines",
                            name="Active Avg",
                            line=dict(color=COLORS["primary_light"], dash="dash"),
                        )
                    )

                pmn_line = price_history.get("pmn")
                if pmn_line:
                    fig.add_hline(
                        y=pmn_line,
                        line_dash="dot",
                        line_color=COLORS["warning"],
                        annotation_text="PMN",
                        annotation_font_color=COLORS["warning"],
                    )

                # PMN confidence band (shaded area between pmn_low and pmn_high)
                pmn_low = price_history.get("pmn_low") or (
                    product.get("pmn_low") if product else None
                )
                pmn_high = price_history.get("pmn_high") or (
                    product.get("pmn_high") if product else None
                )
                if pmn_low is not None and pmn_high is not None and pmn_low < pmn_high:
                    fig.add_hrect(
                        y0=pmn_low,
                        y1=pmn_high,
                        fillcolor=COLORS["warning"],
                        opacity=0.08,
                        line_width=0,
                        annotation_text="PMN band",
                        annotation_position="top left",
                        annotation_font_color=COLORS["muted"],
                        annotation_font_size=10,
                    )

                chart_layout = {**PLOTLY_LAYOUT}
                chart_layout["xaxis"] = {
                    **chart_layout.get("xaxis", {}),
                    "title": "Date",
                }
                chart_layout["yaxis"] = {
                    **chart_layout.get("yaxis", {}),
                    "title": "Price",
                }
                chart_layout["height"] = 320
                fig.update_layout(**chart_layout)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No price history available yet.")

            # ---- Recent sold ----
            st.divider()
            st.markdown("#### Recent Sold (Last 30d)")
            recent_solds: list[dict[str, Any]] = product.get("recent_solds", [])
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
                st.info("No sold items found.")

            # ---- Live listings ----
            st.divider()
            st.markdown("#### Live Listings")
            live_listings: list[dict[str, Any]] = product.get("live_listings", [])
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
                st.info("No active listings found.")

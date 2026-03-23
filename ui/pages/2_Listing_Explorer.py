"""Listing Explorer: browse and filter all ingested listings."""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.lib.api import api_get, fetch_products
from ui.lib.components import paginator
from ui.lib.theme import COLORS, PLOTLY_LAYOUT

st.markdown("# Listing Explorer")
st.caption("Browse and filter all ingested listings")

PAGE_SIZE = 100

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)

with c1:
    filter_source = st.selectbox(
        "Source", ["All", "ebay", "leboncoin", "vinted"], key="explorer_source"
    )
with c2:
    filter_status = st.selectbox("Status", ["All", "Active", "Sold"], key="explorer_status")
with c3:
    filter_search = st.text_input(
        "Search Title",
        placeholder="Search in listing titles...",
        key="explorer_search",
    )
with c4:
    filter_sort = st.selectbox(
        "Sort By",
        [
            "Recent First",
            "Oldest First",
            "Price (Low to High)",
            "Price (High to Low)",
        ],
        key="explorer_sort",
    )

cp1, cp2 = st.columns(2)
with cp1:
    min_price_filter = st.number_input(
        "Min Price", min_value=0.0, value=0.0, step=10.0, key="explorer_min_price"
    )
with cp2:
    max_price_filter = st.number_input(
        "Max Price", min_value=0.0, value=1000.0, step=10.0, key="explorer_max_price"
    )

products = fetch_products()
product_options = ["All Products"] + [f"{p['name']} ({p['brand'] or 'No brand'})" for p in products]
selected_product_filter = st.selectbox("Filter by Product", product_options, key="explorer_product")

show_all_columns = st.toggle("Show all columns", value=False, key="explorer_all_cols")
llm_filter = st.toggle("LLM Validated Only", value=False, key="explorer_llm")

# ---------------------------------------------------------------------------
# Build query — offset driven by paginator session state
# ---------------------------------------------------------------------------
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
if max_price_filter < 1000:
    params["max_price"] = max_price_filter

if selected_product_filter != "All Products":
    product_idx = product_options.index(selected_product_filter) - 1
    if 0 <= product_idx < len(products):
        params["product_id"] = products[product_idx]["product_id"]

if llm_filter:
    params["llm_validated"] = True

sort_map: dict[str, tuple[str, str]] = {
    "Recent First": ("observed_at", "desc"),
    "Oldest First": ("observed_at", "asc"),
    "Price (Low to High)": ("price", "asc"),
    "Price (High to Low)": ("price", "desc"),
}
params["sort_by"], params["sort_order"] = sort_map[filter_sort]

# ---------------------------------------------------------------------------
# Fetch and display
# ---------------------------------------------------------------------------
try:
    with st.spinner("Loading listings..."):
        r = api_get("/listings/explore", params=params)
        r.raise_for_status()
        data = r.json()

    listings = data.get("listings", [])
    total = data.get("total", 0)

    st.metric("Total Matching Listings", total)

    if not listings:
        st.info("No listings found matching your filters. Try adjusting your criteria.")
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
                "observed_at",
                "LLM",
            ]
            extra_cols = [
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
                    "observed_at": "Observed",
                    "url": "URL",
                    "location": "Location",
                    "seller_rating": "Seller Rating",
                    "shipping_cost": "Shipping",
                }
            )
            display_df["Status"] = display_df["Status"].apply(lambda x: "Sold" if x else "Active")

            col_config: dict = {
                "Title": st.column_config.TextColumn("Title", width="large"),
                "Price": st.column_config.NumberColumn("Price", format="%.2f"),
                "Source": st.column_config.TextColumn("Source", width="small"),
            }
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

            if event and event.selection and event.selection.rows:
                sel_idx = event.selection.rows[0]
                if sel_idx < len(df):
                    pid = df.iloc[sel_idx]["product_id"]
                    if st.button("View in Discovery", key="goto_discovery"):
                        st.session_state.selected_product_id = pid
                        st.switch_page("pages/1_Discovery.py")

            paginator("explorer_page", total, PAGE_SIZE)

            st.download_button(
                label="Download as CSV",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name=(f"listings_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"),
                mime="text/csv",
            )

            # ---------------------------------------------------------------
            # Price distribution chart
            # ---------------------------------------------------------------
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
                    hist_layout["yaxis"] = dict(PLOTLY_LAYOUT.get("yaxis", {}), title="Listings")
                    hist_layout["title"] = f"Price Distribution — {len(prices)} listings"
                    fig_hist.update_layout(**hist_layout)
                    st.plotly_chart(fig_hist, use_container_width=True)
                else:
                    st.info("No price data available for the current page.")

            # ---------------------------------------------------------------
            # Statistics + source donut
            # ---------------------------------------------------------------
            st.divider()
            st.subheader("Statistics")

            stat_cols, donut_col = st.columns([3, 2])

            with stat_cols:
                s1, s2, s3, s4 = st.columns(4)
                with s1:
                    avg = df["price"].mean() if "price" in df.columns else None
                    st.metric("Average Price", f"{avg:.2f}" if avg else "N/A")
                with s2:
                    med = df["price"].median() if "price" in df.columns else None
                    st.metric("Median Price", f"{med:.2f}" if med else "N/A")
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

except Exception as exc:
    st.error(f"Failed to load listings: {exc}")

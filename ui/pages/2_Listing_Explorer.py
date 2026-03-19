"""Listing Explorer: browse and filter all ingested listings."""

from datetime import datetime

import pandas as pd
import streamlit as st

from ui.lib.api import api_get, fetch_products

st.header("Listing Database Explorer")
st.write("Browse and filter all ingested listings from the database")

# ---------------------------------------------------------------------------
# Session state for pagination
# ---------------------------------------------------------------------------
if "explorer_page" not in st.session_state:
    st.session_state.explorer_page = 0

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

# Show all columns toggle
show_all_columns = st.toggle("Show all columns", value=False, key="explorer_all_cols")

# LLM validated filter
llm_filter = st.toggle("LLM Validated Only", value=False, key="explorer_llm")

# ---------------------------------------------------------------------------
# Build query
# ---------------------------------------------------------------------------
offset = st.session_state.explorer_page * PAGE_SIZE
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

sort_map = {
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
            # LLM validation badge
            df["LLM"] = df["llm_validated"].apply(lambda x: "Validated" if x else "---")

            # Default columns
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

            col_config = {
                "Title": st.column_config.TextColumn("Title", width="large"),
                "Price": st.column_config.NumberColumn("Price", format="%.2f"),
                "Source": st.column_config.TextColumn("Source", width="small"),
            }
            if "URL" in display_df.columns:
                col_config["URL"] = st.column_config.LinkColumn(
                    "Link", width="small", display_text="View"
                )

            # Clickable table
            event = st.dataframe(
                display_df,
                use_container_width=True,
                height=600,
                hide_index=True,
                column_config=col_config,
                on_select="rerun",
                selection_mode="single-row",
                key="explorer_table",
            )

            # Cross-page navigation to Discovery
            if event and event.selection and event.selection.rows:
                sel_idx = event.selection.rows[0]
                if sel_idx < len(df):
                    pid = df.iloc[sel_idx]["product_id"]
                    if st.button("View in Discovery", key="goto_discovery"):
                        st.session_state.selected_product_id = pid
                        st.switch_page("pages/1_Discovery.py")

            # Pagination
            total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            current_page = st.session_state.explorer_page
            start_item = offset + 1
            end_item = min(offset + len(listings), total)

            p1, p2, p3 = st.columns([1, 2, 1])
            with p1:
                if st.button("Previous", disabled=current_page == 0, key="exp_prev"):
                    st.session_state.explorer_page = current_page - 1
                    st.rerun()
            with p2:
                st.caption(
                    f"Page {current_page + 1} of {total_pages} ({start_item}-{end_item} of {total})"
                )
            with p3:
                if st.button(
                    "Next",
                    disabled=current_page >= total_pages - 1,
                    key="exp_next",
                ):
                    st.session_state.explorer_page = current_page + 1
                    st.rerun()

            # Export
            st.download_button(
                label="Download as CSV",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name=f"listings_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )

            # Statistics
            st.divider()
            st.subheader("Statistics")
            s1, s2, s3, s4 = st.columns(4)
            with s1:
                avg = df["price"].mean()
                st.metric("Average Price", f"{avg:.2f}" if avg else "N/A")
            with s2:
                med = df["price"].median()
                st.metric("Median Price", f"{med:.2f}" if med else "N/A")
            with s3:
                st.metric("Sold Items", int(df["is_sold"].sum()))
            with s4:
                st.metric("Active Listings", int((~df["is_sold"]).sum()))

except Exception as exc:
    st.error(f"Failed to load listings: {exc}")

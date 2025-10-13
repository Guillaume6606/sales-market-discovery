"""
Market Discovery - Arbitrage Opportunity Dashboard
"""

import streamlit as st
import httpx
import os
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from typing import Any, Dict, List, Optional

# Configuration
API = os.environ.get("API_URL", "http://backend:8000")
SUPPORTED_PROVIDERS = ["ebay", "leboncoin", "vinted"]

st.set_page_config(
    page_title="Market Discovery - Arbitrage Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

@st.cache_data(ttl=10)
def fetch_discovery(
    category: str = "",
    brand: str = "",
    min_margin: Optional[float] = None,
    max_margin: Optional[float] = None,
    min_liquidity: Optional[float] = None,
    min_trend: Optional[float] = None,
    sort_by: str = "margin",
    limit: int = 50,
) -> Dict[str, Any]:
    """Fetch discovery opportunities from API"""
    try:
        params = {
            "sort_by": sort_by,
            "limit": limit,
        }
        
        if category:
            params["category"] = category
        if brand:
            params["brand"] = brand
        if min_margin is not None:
            params["min_margin"] = min_margin
        if max_margin is not None:
            params["max_margin"] = max_margin
        if min_liquidity is not None:
            params["min_liquidity"] = min_liquidity
        if min_trend is not None:
            params["min_trend"] = min_trend
        
        r = httpx.get(f"{API}/products/discovery", params=params, timeout=15.0)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Failed to load opportunities: {exc}")
        return {"items": [], "total": 0}


@st.cache_data(ttl=10)
def fetch_product_detail(product_id: str) -> Optional[Dict[str, Any]]:
    """Fetch detailed product information"""
    try:
        r = httpx.get(f"{API}/products/{product_id}", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Failed to load product details: {exc}")
        return None


@st.cache_data(ttl=10)
def fetch_price_history(product_id: str, days: int = 30) -> Optional[Dict[str, Any]]:
    """Fetch price history for charts"""
    try:
        r = httpx.get(f"{API}/products/{product_id}/price-history", params={"days": days}, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return None


@st.cache_data(ttl=30)
def fetch_analytics() -> Optional[Dict[str, Any]]:
    """Fetch analytics overview"""
    try:
        r = httpx.get(f"{API}/analytics/overview", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=30)
def fetch_categories() -> List[str]:
    """Fetch category list"""
    try:
        r = httpx.get(f"{API}/categories", timeout=10.0)
        r.raise_for_status()
        categories = r.json().get("categories", [])
        return [cat["name"] for cat in categories]
    except Exception:
        return []


@st.cache_data(ttl=10)
def fetch_products(active: bool | None = None) -> List[Dict[str, Any]]:
    """Fetch products list"""
    params: Dict[str, Any] = {}
    if active is not None:
        params["is_active"] = str(active).lower()
    try:
        r = httpx.get(f"{API}/products", params=params, timeout=10.0)
        r.raise_for_status()
        return r.json().get("products", [])
    except Exception as exc:
        st.error(f"Failed to load products: {exc}")
        return []


def get_margin_color(delta_pct: Optional[float]) -> str:
    """Return color based on margin percentage"""
    if delta_pct is None:
        return "gray"
    elif delta_pct <= -20:
        return "#00ff00"
    elif delta_pct <= -10:
        return "#90EE90"
    elif delta_pct <= 0:
        return "#D3D3D3"
    else:
        return "#FFB6C6"


def format_liquidity_stars(score: Optional[float]) -> str:
    """Convert liquidity score to star rating"""
    if score is None:
        return "N/A"
    stars = int(score * 5)
    return "⭐" * stars if stars > 0 else "—"


def format_trend_indicator(score: Optional[float]) -> str:
    """Convert trend score to indicator"""
    if score is None:
        return "—"
    elif score > 0.5:
        return "📈 Hot"
    elif score > 0:
        return "→ Stable"
    else:
        return "📉 Cooling"


# Initialize session state
if "selected_product_id" not in st.session_state:
    st.session_state.selected_product_id = None
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Discovery"

# ============================================================================
# MAIN APP
# ============================================================================

st.title("🔎 Market Discovery & PMN")

# Sidebar navigation
with st.sidebar:
    st.header("Navigation")
    tab = st.radio(
        "View",
        ["Discovery", "Listing Explorer", "Product Setup", "Import New Data"],
        key="main_tab",
    )
    st.session_state.active_tab = tab

# ============================================================================
# DISCOVERY TAB
# ============================================================================

if tab == "Discovery":
    with st.sidebar:
        st.divider()
        st.header("🔍 Filters")
        
        # Quick Presets
        st.subheader("Quick Presets")
        preset = st.radio(
            "Select Preset",
            ["🔥 High Margin", "💎 Fast Movers", "✅ Low Risk", "🚀 Trending", "⚙️ Custom"],
            index=4,
            key="preset_select",
        )
        
        # Apply preset defaults
        if preset == "🔥 High Margin":
            default_min_margin, default_max_margin = -50, -20
            default_min_liquidity, default_min_trend = None, None
        elif preset == "💎 Fast Movers":
            default_min_margin, default_max_margin = None, None
            default_min_liquidity, default_min_trend = 0.8, None
        elif preset == "✅ Low Risk":
            default_min_margin, default_max_margin = None, None
            default_min_liquidity, default_min_trend = 0.6, None
        elif preset == "🚀 Trending":
            default_min_margin, default_max_margin = None, None
            default_min_liquidity, default_min_trend = None, 0.5
        else:
            default_min_margin, default_max_margin = None, None
            default_min_liquidity, default_min_trend = None, None
        
        st.divider()
        
        # Advanced Filters
        st.subheader("Advanced Filters")
        search_query = st.text_input("🔎 Search", placeholder="Product name or brand...", key="search")
        categories = fetch_categories()
        selected_category = st.selectbox("📁 Category", ["All"] + categories, key="category_select")
        brand_filter = st.text_input("🏷️ Brand", placeholder="e.g., Sony", key="brand_filter")
        
        # Margin filters
        st.write("**Margin %**")
        margin_range = st.slider(
            "Delta vs PMN",
            min_value=-50,
            max_value=0,
            value=(default_min_margin or -50, default_max_margin or 0),
            step=5,
            key="margin_slider",
        )
        
        # Liquidity filter
        liquidity_filter = st.slider("💧 Min Liquidity", 0.0, 1.0, default_min_liquidity or 0.0, 0.1, key="liquidity")
        
        # Trend filter
        trend_filter = st.slider("📈 Min Trend", -1.0, 2.0, default_min_trend or -1.0, 0.1, key="trend")
        
        # Sort options
        st.divider()
        sort_by = st.selectbox(
            "📊 Sort By",
            ["margin", "liquidity", "trend"],
            format_func=lambda x: {"margin": "💰 Best Margin", "liquidity": "💧 Highest Liquidity", "trend": "📈 Trending"}[x],
            key="sort_select",
        )
        
        st.divider()
        
        # Actions
        col_ref1, col_ref2 = st.columns(2)
        with col_ref1:
            if st.button("🔄 Refresh", use_container_width=True):
                fetch_discovery.clear()
                fetch_analytics.clear()
                st.rerun()

    # Analytics Overview
    analytics = fetch_analytics()
    if analytics:
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("🎯 Opportunities", analytics.get("opportunities_count", 0))
        with col2:
            st.metric("📦 Products", analytics.get("total_products", 0))
        with col3:
            st.metric("📊 Active Listings", analytics.get("active_listings", 0))
        with col4:
            st.metric("✅ Sold Items", analytics.get("sold_items", 0))
        with col5:
            st.metric("⚡ Recent (24h)", analytics.get("recent_observations_24h", 0))
        
        st.divider()

    # Main Layout
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("💎 Discovery Opportunities")
        
        # Fetch discovery data
        category_param = "" if selected_category == "All" else selected_category
        brand_param = brand_filter or ""
        
        min_margin_param = margin_range[0] if margin_range[0] != -50 else None
        max_margin_param = margin_range[1] if margin_range[1] != 0 else None
        liquidity_param = liquidity_filter if liquidity_filter > 0 else None
        trend_param = trend_filter if trend_filter > -1.0 else None
        
        discovery_data = fetch_discovery(
            category=category_param,
            brand=brand_param,
            min_margin=min_margin_param,
            max_margin=max_margin_param,
            min_liquidity=liquidity_param,
            min_trend=trend_param,
            sort_by=sort_by,
            limit=100,
        )
        
        items = discovery_data.get("items", [])
        total = discovery_data.get("total", 0)
        
        if not items:
            st.info("💡 No opportunities found. Try adjusting your filters or ingest data first.")
            st.markdown("""
            **Next Steps:**
            1. Go to **Product Setup** tab to configure products
            2. Use **Import New Data** tab to trigger scraping
            3. Wait a few minutes for data to populate
            4. Return here and click Refresh
            """)
        else:
            df = pd.DataFrame(items)
            
            # Apply search filter
            if search_query:
                df = df[df["title"].str.contains(search_query, case=False, na=False)]
            
            st.caption(f"Showing {len(df)} of {total} opportunities")
            
            # Display table
            for idx, row in df.iterrows():
                bg_color = get_margin_color(row.get("delta_vs_pmn_pct"))
                
                col_row1, col_row2, col_row3, col_row4, col_row5, col_row6 = st.columns([3, 1, 1, 1, 1, 1])
                
                with col_row1:
                    product_display = f"**{row['title']}**"
                    if row.get("brand"):
                        product_display += f" ({row['brand']})"
                    st.markdown(product_display)
                
                with col_row2:
                    pmn_val = row.get("pmn")
                    st.write(f"PMN: €{pmn_val:.2f}" if pmn_val else "PMN: N/A")
                
                with col_row3:
                    price_val = row.get("price_min_market")
                    st.write(f"💸 €{price_val:.2f}" if price_val else "💸 N/A")
                
                with col_row4:
                    delta_val = row.get("delta_vs_pmn_pct")
                    st.write(f"📉 {delta_val:.1f}%" if delta_val else "📉 N/A")
                
                with col_row5:
                    st.write(format_liquidity_stars(row.get("liquidity_score")))
                
                with col_row6:
                    if st.button("👁️ View", key=f"view_{row['product_id']}", use_container_width=True):
                        st.session_state.selected_product_id = row["product_id"]
                        st.rerun()
                
                st.markdown(f"<div style='height:2px;background-color:{bg_color};margin:5px 0;'></div>", unsafe_allow_html=True)

    with col_right:
        st.subheader("📄 Product Detail")
        
        product_id = st.session_state.selected_product_id
        
        if not product_id:
            st.info("👈 Select a product from the list to see details")
        else:
            product = fetch_product_detail(product_id)
            
            if not product:
                st.error("Failed to load product details")
            else:
                # Header
                st.markdown(f"### {product['title']}")
                if product.get("brand"):
                    st.caption(f"🏷️ {product['brand']}")
                if product.get("category"):
                    st.caption(f"📁 {product['category']}")
                
                st.divider()
                
                # Market Summary
                st.markdown("#### 💰 Market Summary")
                
                col_m1, col_m2 = st.columns(2)
                with col_m1:
                    pmn_val = product.get("pmn")
                    if pmn_val:
                        st.metric("PMN (Ref)", f"€{pmn_val:.2f}")
                        st.caption(f"Range: €{product.get('pmn_low', 0):.2f} - €{product.get('pmn_high', 0):.2f}")
                    else:
                        st.metric("PMN (Ref)", "N/A")
                
                with col_m2:
                    median_30d = product.get("price_median_30d")
                    st.metric("Median 30d", f"€{median_30d:.2f}" if median_30d else "N/A")
                
                col_m3, col_m4 = st.columns(2)
                with col_m3:
                    liq = product.get("liquidity_score")
                    st.metric("💧 Liquidity", f"{liq:.2f}" if liq is not None else "N/A")
                
                with col_m4:
                    trend = product.get("trend_score")
                    st.metric("📈 Trend", f"{trend:.2f}" if trend is not None else "N/A")
                
                st.divider()
                
                # Price History Chart
                st.markdown("#### 📈 Price History")
                price_history = fetch_price_history(product_id, days=30)
                
                if price_history and (price_history.get("sold_history") or price_history.get("active_history")):
                    fig = go.Figure()
                    
                    sold_hist = price_history.get("sold_history", [])
                    if sold_hist:
                        dates = [h["date"] for h in sold_hist]
                        prices = [h["avg_price"] for h in sold_hist]
                        fig.add_trace(go.Scatter(x=dates, y=prices, mode="markers+lines", name="Sold Avg", line=dict(color="green")))
                    
                    active_hist = price_history.get("active_history", [])
                    if active_hist:
                        dates = [h["date"] for h in active_hist]
                        prices = [h["avg_price"] for h in active_hist]
                        fig.add_trace(go.Scatter(x=dates, y=prices, mode="markers+lines", name="Active Avg", line=dict(color="blue", dash="dash")))
                    
                    pmn = price_history.get("pmn")
                    if pmn:
                        fig.add_hline(y=pmn, line_dash="dot", line_color="red", annotation_text="PMN")
                    
                    fig.update_layout(xaxis_title="Date", yaxis_title="Price (€)", height=300, margin=dict(l=0, r=0, t=20, b=0))
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("No price history available yet")
                
                st.divider()
                
                # Recent Sold Items
                st.markdown("#### ✅ Recent Sold (Last 30d)")
                recent_solds = product.get("recent_solds", [])
                
                if recent_solds:
                    sold_df = pd.DataFrame(recent_solds[:5])
                    sold_df["price_display"] = sold_df["price"].apply(lambda x: f"€{x:.2f}" if x else "N/A")
                    sold_df["date"] = pd.to_datetime(sold_df["observed_at"]).dt.strftime("%Y-%m-%d")
                    st.dataframe(sold_df[["date", "price_display", "condition", "source"]], hide_index=True, use_container_width=True)
                else:
                    st.info("No sold items found")
                
                st.divider()
                
                # Live Listings
                st.markdown("#### 🔴 Live Listings")
                live_listings = product.get("live_listings", [])
                
                if live_listings:
                    live_df = pd.DataFrame(live_listings[:5])
                    live_df["price_display"] = live_df["price"].apply(lambda x: f"€{x:.2f}" if x else "N/A")
                    st.dataframe(live_df[["price_display", "condition", "location", "source"]], hide_index=True, use_container_width=True)
                else:
                    st.info("No active listings found")

# ============================================================================
# LISTING EXPLORER TAB
# ============================================================================

elif tab == "Listing Explorer":
    st.subheader("🗂️ Listing Database Explorer")
    st.write("Browse and filter all ingested listings from the database")
    
    # Filters in columns
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        filter_source = st.selectbox(
            "Source",
            ["All", "ebay", "leboncoin", "vinted"],
            key="explorer_source"
        )

    with col2:
        filter_status = st.selectbox(
            "Status",
            ["All", "Active", "Sold"],
            key="explorer_status"
        )
    
    with col3:
        filter_search = st.text_input(
            "Search Title",
            placeholder="Search in listing titles...",
            key="explorer_search"
        )
    
    with col4:
        filter_sort = st.selectbox(
            "Sort By",
            ["Recent First", "Oldest First", "Price (Low to High)", "Price (High to Low)"],
            key="explorer_sort"
        )
    
    # Price range filter
    col_price1, col_price2 = st.columns(2)
    with col_price1:
        min_price_filter = st.number_input(
            "Min Price (€)",
            min_value=0.0,
            value=0.0,
            step=10.0,
            key="explorer_min_price"
        )
    with col_price2:
        max_price_filter = st.number_input(
            "Max Price (€)",
            min_value=0.0,
            value=1000.0,
            step=10.0,
            key="explorer_max_price"
        )
    
    # Product filter
    products = fetch_products()
    product_options = ["All Products"] + [f"{p['name']} ({p['brand'] or 'No brand'})" for p in products]
    selected_product_filter = st.selectbox(
        "Filter by Product",
        product_options,
        key="explorer_product"
    )
    
    # Build query parameters
    params = {
        "limit": 100,
        "offset": 0,
    }
    
    if filter_source != "All":
        params["source"] = filter_source
    
    if filter_status == "Active":
        params["is_sold"] = "false"
    elif filter_status == "Sold":
        params["is_sold"] = "true"
    
    if filter_search:
        params["search"] = filter_search
    
    if min_price_filter > 0:
        params["min_price"] = min_price_filter
    
    if max_price_filter < 1000:
        params["max_price"] = max_price_filter
    
    if selected_product_filter != "All Products":
        # Extract product_id from selection
        product_idx = product_options.index(selected_product_filter) - 1
        if product_idx >= 0 and product_idx < len(products):
            params["product_id"] = products[product_idx]["product_id"]
    
    # Map sort selection to API parameters
    if filter_sort == "Recent First":
        params["sort_by"] = "observed_at"
        params["sort_order"] = "desc"
    elif filter_sort == "Oldest First":
        params["sort_by"] = "observed_at"
        params["sort_order"] = "asc"
    elif filter_sort == "Price (Low to High)":
        params["sort_by"] = "price"
        params["sort_order"] = "asc"
    elif filter_sort == "Price (High to Low)":
        params["sort_by"] = "price"
        params["sort_order"] = "desc"
    
    # Fetch listings
    try:
        r = httpx.get(f"{API}/listings/explore", params=params, timeout=15.0)
        r.raise_for_status()
        data = r.json()
        
        listings = data.get("listings", [])
        total = data.get("total", 0)
        
        # Display summary
        st.metric("Total Matching Listings", total)

        if not listings:
            st.info("📭 No listings found matching your filters. Try adjusting your criteria.")
        else:
            # Convert to DataFrame for better display
            df = pd.DataFrame(listings)

            # Format columns
            if not df.empty:
                # Prepare display DataFrame
                display_df = df[[
                    "product_name",
                    "product_brand",
                    "title",
                    "price",
                    "source",
                    "is_sold",
                    "condition",
                    "url",
                    "location",
                    "observed_at"
                ]].copy()
                
                display_df.columns = [
                    "Product",
                    "Brand",
                    "Title",
                    "Price (€)",
                    "Source",
                    "Sold",
                    "Condition",
                    "URL",
                    "Location",
                    "Observed"
                ]
                
                # Format the DataFrame
                display_df["Sold"] = display_df["Sold"].apply(lambda x: "✅ Yes" if x else "📦 Active")
                display_df["Price (€)"] = display_df["Price (€)"].apply(lambda x: f"€{x:.2f}" if x else "N/A")
                display_df["Condition"] = display_df["Condition"].fillna("Unknown")
                
                # Display the table
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    height=600,
                    column_config={
                        "Title": st.column_config.TextColumn(
                            "Title",
                            width="large",
                        ),
                        "Price (€)": st.column_config.TextColumn(
                            "Price (€)",
                            width="small",
                        ),
                        "Source": st.column_config.TextColumn(
                            "Source",
                            width="small",
                        ),
                        "URL": st.column_config.LinkColumn(
                            "Link",
                            width="small",
                            display_text="🔗 View"
                        ),
                    }
                )
                
                # Export option
                st.download_button(
                    label="📥 Download as CSV",
                    data=df.to_csv(index=False).encode('utf-8'),
                    file_name=f"listings_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                )
                
                # Statistics
                st.divider()
                st.subheader("📊 Statistics")
                
                stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
                
                with stat_col1:
                    avg_price = df["price"].mean()
                    st.metric("Average Price", f"€{avg_price:.2f}" if avg_price else "N/A")
                
                with stat_col2:
                    median_price = df["price"].median()
                    st.metric("Median Price", f"€{median_price:.2f}" if median_price else "N/A")
                
                with stat_col3:
                    sold_count = df["is_sold"].sum()
                    st.metric("Sold Items", sold_count)
                
                with stat_col4:
                    active_count = len(df) - sold_count
                    st.metric("Active Listings", active_count)
                
    except Exception as exc:
        st.error(f"Failed to load listings: {exc}")

# ============================================================================
# PRODUCT SETUP TAB (Keep existing functionality)
# ============================================================================

elif tab == "Product Setup":
    st.subheader("🗂️ Manage Categories")
    categories_list = fetch_categories()

    with st.form("create_category", clear_on_submit=True):
        st.write("Create a new category")
        new_cat_name = st.text_input("Name", key="new_category_name")
        new_cat_desc = st.text_area("Description", key="new_category_desc")
        if st.form_submit_button("Create Category", type="primary"):
            payload = {"name": new_cat_name.strip(), "description": new_cat_desc or None}
            try:
                r = httpx.post(f"{API}/categories", json=payload, timeout=10.0)
                if r.status_code == 201:
                    st.success("Category created")
                    fetch_categories.clear()
                    st.rerun()
                else:
                    st.error(f"Creation failed: {r.text}")
            except Exception as exc:
                st.error(f"API error: {exc}")

    st.divider()
    st.subheader("🛍️ Manage Products")

    products = fetch_products()
    if products:
        products_df = pd.DataFrame(products)
        display_cols = ["name", "search_query", "brand", "providers", "is_active"]
        st.dataframe(products_df[display_cols], hide_index=True, use_container_width=True)
    else:
        st.info("No products configured yet.")

    # Product Management Mode Selection
    st.markdown("### Product Management")
    mode = st.radio("Action", ["➕ Create New Product", "✏️ Edit Existing Product"], key="product_mode", horizontal=True)
    
    # Initialize session state for edit mode
    if "selected_edit_product" not in st.session_state:
        st.session_state.selected_edit_product = None
    
    # Edit Mode: Product Selection
    selected_product = None
    if mode == "✏️ Edit Existing Product":
        if not products:
            st.warning("No products available to edit. Create one first!")
        else:
            product_options = {f"{p['name']} ({p['brand'] or 'No brand'})": p for p in products}
            selected_label = st.selectbox(
                "Select Product to Edit",
                options=list(product_options.keys()),
                key="edit_product_select"
            )
            selected_product = product_options[selected_label]
            st.session_state.selected_edit_product = selected_product
    
    # Product Form (Create or Edit)
    if mode == "➕ Create New Product" or (mode == "✏️ Edit Existing Product" and selected_product):
        with st.form("product_form", clear_on_submit=False):
            # Pre-fill with existing data if editing
            default_name = selected_product['name'] if selected_product else ""
            default_desc = selected_product.get('description', '') if selected_product else ""
            default_query = selected_product['search_query'] if selected_product else ""
            default_brand = selected_product.get('brand', '') if selected_product else ""
            default_price_min = str(selected_product.get('price_min', '')) if selected_product and selected_product.get('price_min') else ""
            default_price_max = str(selected_product.get('price_max', '')) if selected_product and selected_product.get('price_max') else ""
            default_providers = selected_product.get('providers', SUPPORTED_PROVIDERS) if selected_product else SUPPORTED_PROVIDERS
            default_active = selected_product.get('is_active', True) if selected_product else True
            
            # Get current category name if editing
            default_category = None
            if selected_product and selected_product.get('category'):
                default_category = selected_product['category']['name']
            
            # Form fields
            pf_name = st.text_input("Name *", value=default_name, key="pf_name")
            pf_desc = st.text_area("Description", value=default_desc, key="pf_desc")
            pf_query = st.text_input("Search Query *", value=default_query, key="pf_query")
            
            # Category selection
            category_names = [cat for cat in categories_list] if categories_list else []
            default_cat_index = category_names.index(default_category) if default_category in category_names else 0
            pf_category = st.selectbox(
                "Category *",
                options=category_names,
                index=default_cat_index,
                key="pf_category"
            )
            
            pf_brand = st.text_input("Brand (optional)", value=default_brand, key="pf_brand")
            
            col1, col2 = st.columns(2)
            with col1:
                pf_price_min = st.text_input("Min Price (€)", value=default_price_min, key="pf_price_min")
            with col2:
                pf_price_max = st.text_input("Max Price (€)", value=default_price_max, key="pf_price_max")
            
            pf_providers = st.multiselect(
                "Providers",
                options=SUPPORTED_PROVIDERS,
                default=default_providers,
                key="pf_providers"
            )
            pf_active = st.checkbox("Active", value=default_active, key="pf_active")
            
            # Submit button
            button_label = "💾 Update Product" if selected_product else "➕ Create Product"
            if st.form_submit_button(button_label, type="primary"):
                if not pf_name.strip() or not pf_query.strip() or not pf_category:
                    st.error("Name, search query, and category are required.")
                else:
                    try:
                        # Get category ID
                        r = httpx.get(f"{API}/categories", timeout=10.0)
                        all_categories = r.json().get("categories", [])
                        category_id = next((cat["category_id"] for cat in all_categories if cat["name"] == pf_category), None)
                        
                        if not category_id:
                            st.error("Selected category not found")
                        else:
                            payload = {
                                "name": pf_name.strip(),
                                "description": pf_desc.strip() if pf_desc.strip() else None,
                                "search_query": pf_query.strip(),
                                "category_id": category_id,
                                "brand": pf_brand.strip() if pf_brand.strip() else None,
                                "price_min": float(pf_price_min) if pf_price_min else None,
                                "price_max": float(pf_price_max) if pf_price_max else None,
                                "providers": pf_providers,
                                "is_active": pf_active,
                            }
                            
                            # Create or Update
                            if selected_product:
                                # Update existing product
                                r = httpx.put(
                                    f"{API}/products/{selected_product['product_id']}",
                                    json=payload,
                                    timeout=10.0
                                )
                                if r.status_code == 200:
                                    st.success(f"✅ Product '{pf_name}' updated successfully!")
                                    fetch_products.clear()
                                    st.session_state.selected_edit_product = None
                                    st.rerun()
                                else:
                                    st.error(f"Update failed: {r.text}")
                            else:
                                # Create new product
                                r = httpx.post(f"{API}/products", json=payload, timeout=10.0)
                                if r.status_code == 201:
                                    st.success(f"✅ Product '{pf_name}' created successfully!")
                                    fetch_products.clear()
                                    st.rerun()
                                else:
                                    st.error(f"Creation failed: {r.text}")
                    except ValueError:
                        st.error("Invalid price values. Please enter valid numbers.")
                    except Exception as exc:
                        st.error(f"API error: {exc}")

# ============================================================================
# IMPORT NEW DATA TAB
# ============================================================================

elif tab == "Import New Data":
    st.subheader("🚀 Import New Data")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.write("Select a configured product to launch ingestion jobs")

        products = fetch_products()
        if not products:
            st.warning("No products available. Configure them in the Product Setup tab.")
        else:
            product_map = {f"{prod['name']} ({prod['product_id'][:8]})": prod for prod in products}
            selected_label = st.selectbox("Product", list(product_map.keys()), key="import_product")
            selected_product = product_map[selected_label]

            provider_selection = st.multiselect("Providers", options=SUPPORTED_PROVIDERS, default=selected_product.get("providers") or SUPPORTED_PROVIDERS)

            import_limit = st.slider("Max Items", 10, 100, 50, key="import_limit")

            col_imp_a, col_imp_b, col_imp_c = st.columns(3)
            with col_imp_a:
                if st.button("Queue Listings", use_container_width=True):
                    try:
                        r = httpx.post(f"{API}/ingestion/trigger", params={"product_id": selected_product["product_id"], "sold_limit": 0, "listings_limit": import_limit, "sources": provider_selection}, timeout=20.0)
                        if r.status_code == 200:
                            st.success("Listings ingestion queued")
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"API error: {exc}")
            with col_imp_b:
                if st.button("Queue Sold", use_container_width=True):
                    try:
                        r = httpx.post(f"{API}/ingestion/trigger", params={"product_id": selected_product["product_id"], "sold_limit": import_limit, "listings_limit": 0, "sources": provider_selection}, timeout=20.0)
                        if r.status_code == 200:
                            st.success("Sold ingestion queued")
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"API error: {exc}")
            with col_imp_c:
                if st.button("Queue Full Run", type="primary", use_container_width=True):
                    try:
                        r = httpx.post(f"{API}/ingestion/trigger", params={"product_id": selected_product["product_id"], "sold_limit": import_limit, "listings_limit": import_limit, "sources": provider_selection}, timeout=20.0)
                        if r.status_code == 200:
                            st.success("Full ingestion queued")
                        else:
                            st.error(f"Failed: {r.text}")
                    except Exception as exc:
                        st.error(f"API error: {exc}")

    with col2:
        st.subheader("Status")
        try:
            r = httpx.get(f"{API}/ingestion/status", timeout=10.0)
            if r.status_code == 200:
                status = r.json()
                st.metric("Total Products", status.get("total_products", 0))
                st.metric("Total Observations", status.get("total_observations", 0))
                st.metric("Active Listings", status.get("active_listings", 0))
                st.metric("Sold Items", status.get("sold_observations", 0))

                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("eBay", status.get("ebay_observations", 0))
                with col2:
                    st.metric("LBC", status.get("leboncoin_observations", 0))
                with col3:
                    st.metric("Vinted", status.get("vinted_observations", 0))
            else:
                st.error("Could not fetch status")
        except Exception as e:
            st.error(f"Status check failed: {e}")

        st.subheader("Quick Actions")
        if st.button("Refresh Status"):
            st.rerun()

st.divider()
st.caption("Market Discovery Dashboard | Real-time arbitrage opportunities")

import streamlit as st
import httpx
import os
import pandas as pd
from datetime import datetime

API = os.environ.get("API_URL", "http://backend:8000")

st.set_page_config(page_title="Market Discovery", layout="wide")

st.title("🔎 Market Discovery & PMN")

# Initialize session state for active tab
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Opportunities"

# Sidebar filters (shared across tabs)
with st.sidebar:
    st.header("Global Filters")
    category = st.text_input("Category", key="global_category")
    brand = st.text_input("Brand", key="global_brand")

    # Tab selection
    tab = st.radio("View", ["Opportunities", "Listings Explorer", "Import New Data"], key="main_tab")

    if tab == "Opportunities":
        min_margin = st.slider("Min % below PMN", -50, 0, -20, key="opportunities_margin")
    elif tab == "Listings Explorer":
        st.subheader("Listings Filters")
        provider = st.selectbox("Provider", ["All", "eBay", "LeBonCoin", "Vinted"], key="listings_provider")
        price_min = st.number_input("Min Price (€)", min_value=0, value=0, key="listings_price_min")
        price_max = st.number_input("Max Price (€)", min_value=0, value=10000, key="listings_price_max")
        condition = st.selectbox("Condition", ["All", "New", "Like New", "Good", "Fair"], key="listings_condition")
        show_sold = st.checkbox("Include Sold Items", value=False, key="listings_show_sold")
    elif tab == "Import New Data":
        st.subheader("Import Configuration")
        import_keyword = st.text_input("Keyword to Import", key="import_keyword")
        import_provider = st.selectbox("Provider", ["eBay", "LeBonCoin", "Vinted", "All"], key="import_provider")
        import_limit = st.slider("Max Items", 10, 100, 50, key="import_limit")

# Main content area based on selected tab
if tab == "Opportunities":
    col1, col2 = st.columns([3,2])

    with col1:
        st.subheader("Opportunities")
        params = {"category": category, "brand": brand, "min_margin": min_margin}
        try:
            r = httpx.get(f"{API}/products/discovery", params=params, timeout=10.0)
            items = r.json().get("items", [])
        except Exception as e:
            st.error(f"API error: {e}")
            items = []
        for it in items:
            with st.container(border=True):
                st.markdown(f"**{it['title']}** — {it.get('brand','?')}")
                st.caption(f"PMN: {it.get('pmn')} | Best price: {it.get('price_min_market')} | Δ%: {it.get('delta_vs_pmn_pct')} | Liquidity: {it.get('liquidity_score')} | Trend: {it.get('trend_score')}")
                if st.button("View", key=it["product_id"]):
                    st.session_state["selected"] = it["product_id"]

    with col2:
        st.subheader("Product")
        pid = st.session_state.get("selected")
        if pid:
            try:
                r = httpx.get(f"{API}/products/{pid}", timeout=10.0)
                p = r.json()
                st.markdown(f"### {p['title']} — {p.get('brand','?')}")
                st.metric("PMN", p.get("pmn"))
                st.caption(f"Range: {p.get('pmn_low')} — {p.get('pmn_high')}")
                st.write("Recent solds", p.get("recent_solds", []))
                st.write("Live listings", p.get("live_listings", []))
            except Exception as e:
                st.error(f"API error: {e}")
        else:
            st.info("Select a product from the left list to see details.")

elif tab == "Listings Explorer":
    st.subheader("📦 Listings Explorer")

    # Build filter parameters
    filter_params = {}
    if category:
        filter_params["category"] = category
    if brand:
        filter_params["brand"] = brand
    if provider != "All":
        filter_params["source"] = provider.lower()
    if price_min > 0:
        filter_params["price_min"] = price_min
    if price_max < 10000:
        filter_params["price_max"] = price_max
    if condition != "All":
        filter_params["condition"] = condition.lower().replace(" ", "_")
    if show_sold:
        filter_params["include_sold"] = "true"

    try:
        r = httpx.get(f"{API}/listings", params=filter_params, timeout=10.0)
        listings_data = r.json()
        listings = listings_data.get("listings", [])
    except Exception as e:
        st.error(f"API error: {e}")
        listings = []

    if not listings:
        st.info("No listings found matching your criteria.")
    else:
        # Convert to DataFrame for better display
        df = pd.DataFrame(listings)

        # Format the data for display
        if not df.empty:
            df_display = df.copy()
            df_display['price'] = df_display['price'].apply(lambda x: f"€{x:.2f}" if x else "N/A")
            df_display['observed_at'] = pd.to_datetime(df_display['observed_at']).dt.strftime('%Y-%m-%d %H:%M')
            df_display['seller_rating'] = df_display['seller_rating'].apply(lambda x: f"{x:.1f}⭐" if x else "N/A")

            # Add condition emoji
            condition_emojis = {
                "new": "🆕",
                "like_new": "✨",
                "good": "✅",
                "fair": "⚠️"
            }
            df_display['condition_norm'] = df_display['condition_norm'].apply(
                lambda x: f"{condition_emojis.get(x, '❓')} {x.replace('_', ' ').title()}" if x else "Unknown"
            )

            st.dataframe(
                df_display[['title', 'price', 'condition_norm', 'location', 'seller_rating', 'source', 'is_sold', 'observed_at']],
                use_container_width=True,
                column_config={
                    "title": st.column_config.TextColumn("Title", width="large"),
                    "price": st.column_config.TextColumn("Price", width="small"),
                    "condition_norm": st.column_config.TextColumn("Condition", width="small"),
                    "location": st.column_config.TextColumn("Location", width="small"),
                    "seller_rating": st.column_config.TextColumn("Rating", width="small"),
                    "source": st.column_config.TextColumn("Provider", width="small"),
                    "is_sold": st.column_config.CheckboxColumn("Sold", width="small"),
                    "observed_at": st.column_config.TextColumn("Date", width="small"),
                }
            )

            st.caption(f"Showing {len(listings)} listings")

            # Add a button to trigger import for selected listings
            if st.button("Import Selected Listings", type="primary"):
                # For now, trigger import for the current search
                st.info("Import functionality would trigger ingestion for current search criteria")

elif tab == "Import New Data":
    st.subheader("🚀 Import New Data")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.write("Configure what data to import:")

        if not import_keyword:
            st.warning("Please enter a keyword to import")
            st.stop()

        # Import configuration display
        st.write("**Import Configuration:**")
        st.write(f"- **Keyword:** {import_keyword}")
        st.write(f"- **Provider:** {import_provider}")
        st.write(f"- **Max Items:** {import_limit}")

        # Import buttons
        if st.button("Start Import", type="primary", use_container_width=True):
            with st.spinner("Importing data..."):
                try:
                    if import_provider == "eBay":
                        r = httpx.post(f"{API}/ingestion/trigger", params={"keyword": import_keyword, "sold_limit": import_limit, "listings_limit": import_limit}, timeout=30.0)
                    elif import_provider == "LeBonCoin":
                        r = httpx.post(f"{API}/ingestion/leboncoin/trigger", params={"keyword": import_keyword, "listings_limit": import_limit}, timeout=30.0)
                    elif import_provider == "Vinted":
                        r = httpx.post(f"{API}/ingestion/vinted/trigger", params={"keyword": import_keyword, "listings_limit": import_limit}, timeout=30.0)
                    else:  # All
                        # Trigger all imports
                        r1 = httpx.post(f"{API}/ingestion/trigger", params={"keyword": import_keyword, "sold_limit": import_limit, "listings_limit": import_limit}, timeout=30.0)
                        r2 = httpx.post(f"{API}/ingestion/leboncoin/trigger", params={"keyword": import_keyword, "listings_limit": import_limit}, timeout=30.0)
                        r3 = httpx.post(f"{API}/ingestion/vinted/trigger", params={"keyword": import_keyword, "listings_limit": import_limit}, timeout=30.0)
                        r = r1  # Use first response for status

                    if r.status_code == 200:
                        st.success(f"Import started successfully! Check status at /ingestion/status")
                    else:
                        st.error(f"Import failed: {r.text}")

                except Exception as e:
                    st.error(f"Import error: {e}")

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

                # Show source breakdown
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("eBay Items", status.get("ebay_observations", 0))
                with col2:
                    st.metric("LeBonCoin Items", status.get("leboncoin_observations", 0))
                with col3:
                    st.metric("Vinted Items", status.get("vinted_observations", 0))
            else:
                st.error("Could not fetch status")
        except Exception as e:
            st.error(f"Status check failed: {e}")

        # Recent activity
        st.subheader("Quick Actions")
        if st.button("Refresh Status"):
            st.rerun()

        if st.button("Import Sample Data (iPhone)"):
            try:
                r = httpx.post(f"{API}/ingestion/leboncoin/trigger", params={"keyword": "iPhone", "listings_limit": 20}, timeout=30.0)
                if r.status_code == 200:
                    st.success("Sample import started!")
                else:
                    st.error("Sample import failed")
            except Exception as e:
                st.error(f"Sample import error: {e}")

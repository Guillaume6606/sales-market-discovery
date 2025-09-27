import streamlit as st
import httpx
import os

API = os.environ.get("API_URL", "http://backend:8000")

st.set_page_config(page_title="Market Discovery", layout="wide")

st.title("🔎 Market Discovery & PMN")

with st.sidebar:
    st.header("Filters")
    category = st.text_input("Category")
    brand = st.text_input("Brand")
    min_margin = st.slider("Min % below PMN", -50, 0, -20)

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

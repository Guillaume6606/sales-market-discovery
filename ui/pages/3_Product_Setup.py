"""Product Setup: manage categories and products."""

import pandas as pd
import streamlit as st

from ui.lib.api import (
    api_get,
    api_post,
    api_put,
    fetch_categories,
    fetch_category_names,
    fetch_ingestion_runs,
    fetch_products,
)
from ui.lib.components import kpi_row
from ui.lib.config import SUPPORTED_PROVIDERS
from ui.lib.formatters import relative_time

st.markdown("# Product Setup")
st.caption("Manage categories and configure products for tracking")

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
st.header("Manage Categories")
categories_list = fetch_category_names()

with st.form("create_category", clear_on_submit=True):
    st.write("Create a new category")
    new_cat_name = st.text_input("Name", key="new_category_name")
    new_cat_desc = st.text_area("Description", key="new_category_desc")
    if st.form_submit_button("Create Category", type="primary"):
        payload = {"name": new_cat_name.strip(), "description": new_cat_desc or None}
        try:
            r = api_post("/categories", json=payload, timeout=10.0)
            if r.status_code == 201:
                st.success("Category created")
                fetch_categories.clear()
                st.rerun()
            else:
                st.error(f"Creation failed: {r.text}")
        except Exception as exc:
            st.error(f"API error: {exc}")

# ---------------------------------------------------------------------------
# Products table + KPI row
# ---------------------------------------------------------------------------
st.divider()
st.header("Manage Products")

products = fetch_products()
if products:
    total_products = len(products)
    active_products = sum(1 for p in products if p.get("is_active"))
    kpi_row(
        [
            {"label": "Total Products", "value": total_products},
            {"label": "Active Products", "value": active_products},
            {"label": "Inactive Products", "value": total_products - active_products},
        ]
    )

    products_df = pd.DataFrame(products)
    if "last_ingested_at" in products_df.columns:
        products_df["Last Ingested"] = products_df["last_ingested_at"].apply(relative_time)
    display_cols = ["name", "search_query", "brand", "providers", "is_active"]
    if "Last Ingested" in products_df.columns:
        display_cols.append("Last Ingested")
    st.dataframe(products_df[display_cols], hide_index=True, use_container_width=True)
else:
    st.info("No products configured yet.")

# ---------------------------------------------------------------------------
# Product form — uses session state + expanders (no st.form wrapper)
# ---------------------------------------------------------------------------
st.markdown("### Product Management")
mode = st.radio(
    "Action",
    ["Create New Product", "Edit Existing Product"],
    key="product_mode",
    horizontal=True,
)

if "selected_edit_product" not in st.session_state:
    st.session_state.selected_edit_product = None

selected_product: dict | None = None
if mode == "Edit Existing Product":
    if not products:
        st.warning("No products available to edit. Create one first!")
    else:
        product_options = {f"{p['name']} ({p['brand'] or 'No brand'})": p for p in products}
        selected_label = st.selectbox(
            "Select Product to Edit",
            options=list(product_options.keys()),
            key="edit_product_select",
        )
        selected_product = product_options[selected_label]
        st.session_state.selected_edit_product = selected_product

is_editing = selected_product is not None

if mode == "Create New Product" or (mode == "Edit Existing Product" and selected_product):
    # Derive defaults from the selected product (or blank for create)
    default_name = selected_product["name"] if selected_product else ""
    default_desc = selected_product.get("description", "") if selected_product else ""
    default_query = selected_product["search_query"] if selected_product else ""
    default_brand = selected_product.get("brand", "") if selected_product else ""
    default_price_min = (
        float(selected_product["price_min"])
        if selected_product and selected_product.get("price_min")
        else 0.0
    )
    default_price_max = (
        float(selected_product["price_max"])
        if selected_product and selected_product.get("price_max")
        else 0.0
    )
    default_providers = (
        selected_product.get("providers", SUPPORTED_PROVIDERS)
        if selected_product
        else SUPPORTED_PROVIDERS
    )
    default_active = selected_product.get("is_active", True) if selected_product else True
    default_words = (
        ", ".join(selected_product.get("words_to_avoid", [])) if selected_product else ""
    )
    default_llm = (
        selected_product.get("enable_llm_validation", False) if selected_product else False
    )
    default_category: str | None = None
    if selected_product and selected_product.get("category"):
        default_category = selected_product["category"]["name"]

    # ---------------------------------------------------------------------------
    # Required fields — always visible
    # ---------------------------------------------------------------------------
    category_names = categories_list if categories_list else []
    default_cat_index = (
        category_names.index(default_category)
        if default_category and default_category in category_names
        else 0
    )

    pf_name = st.text_input("Name *", value=default_name, key="pf_name")
    pf_desc = st.text_area("Description", value=default_desc, key="pf_desc")
    pf_query = st.text_input("Search Query *", value=default_query, key="pf_query")
    pf_category = st.selectbox(
        "Category *",
        options=category_names,
        index=default_cat_index,
        key="pf_category",
    )

    # ---------------------------------------------------------------------------
    # Pricing & Filters expander
    # ---------------------------------------------------------------------------
    with st.expander("Pricing & Filters", expanded=is_editing):
        pf_brand = st.text_input("Brand (optional)", value=default_brand, key="pf_brand")

        col1, col2 = st.columns(2)
        with col1:
            pf_price_min = st.number_input(
                "Min Price",
                min_value=0.0,
                value=default_price_min,
                step=10.0,
                format="%.2f",
                key="pf_price_min",
            )
        with col2:
            pf_price_max = st.number_input(
                "Max Price",
                min_value=0.0,
                value=default_price_max,
                step=10.0,
                format="%.2f",
                key="pf_price_max",
            )

        pf_words = st.text_area(
            "Words to Avoid (comma-separated)",
            value=default_words,
            key="pf_words",
            help="Listings with these words in the title will be rejected",
        )

    # ---------------------------------------------------------------------------
    # Advanced Settings expander
    # ---------------------------------------------------------------------------
    with st.expander("Advanced Settings", expanded=is_editing):
        pf_providers = st.multiselect(
            "Providers",
            options=SUPPORTED_PROVIDERS,
            default=default_providers,
            key="pf_providers",
        )

        pf_llm = st.checkbox(
            "Enable LLM Validation",
            value=default_llm,
            key="pf_llm",
        )

        pf_active = st.checkbox("Active", value=default_active, key="pf_active")

    # ---------------------------------------------------------------------------
    # Submit button (outside expanders — required when not using st.form)
    # ---------------------------------------------------------------------------
    button_label = "Update Product" if selected_product else "Create Product"
    if st.button(button_label, type="primary"):
        pf_name_val: str = st.session_state.get("pf_name", "")
        pf_query_val: str = st.session_state.get("pf_query", "")
        pf_category_val: str = st.session_state.get("pf_category", "")
        pf_desc_val: str = st.session_state.get("pf_desc", "")
        pf_brand_val: str = st.session_state.get("pf_brand", "")
        pf_price_min_val: float = st.session_state.get("pf_price_min", 0.0)
        pf_price_max_val: float = st.session_state.get("pf_price_max", 0.0)
        pf_words_val: str = st.session_state.get("pf_words", "")
        pf_providers_val: list[str] = st.session_state.get("pf_providers", SUPPORTED_PROVIDERS)
        pf_llm_val: bool = st.session_state.get("pf_llm", False)
        pf_active_val: bool = st.session_state.get("pf_active", True)

        if not pf_name_val.strip() or not pf_query_val.strip() or not pf_category_val:
            st.error("Name, search query, and category are required.")
        else:
            try:
                r = api_get("/categories", timeout=10.0)
                all_categories = r.json().get("categories", [])
                category_id = next(
                    (
                        cat["category_id"]
                        for cat in all_categories
                        if cat["name"] == pf_category_val
                    ),
                    None,
                )

                if not category_id:
                    st.error("Selected category not found")
                else:
                    words_list = (
                        [w.strip() for w in pf_words_val.split(",") if w.strip()]
                        if pf_words_val.strip()
                        else []
                    )
                    payload = {
                        "name": pf_name_val.strip(),
                        "description": (pf_desc_val.strip() if pf_desc_val.strip() else None),
                        "search_query": pf_query_val.strip(),
                        "category_id": category_id,
                        "brand": (pf_brand_val.strip() if pf_brand_val.strip() else None),
                        "price_min": pf_price_min_val if pf_price_min_val > 0 else None,
                        "price_max": pf_price_max_val if pf_price_max_val > 0 else None,
                        "providers": pf_providers_val,
                        "words_to_avoid": words_list,
                        "enable_llm_validation": pf_llm_val,
                        "is_active": pf_active_val,
                    }

                    if selected_product:
                        r = api_put(
                            f"/products/{selected_product['product_id']}",
                            json=payload,
                            timeout=10.0,
                        )
                        if r.status_code == 200:
                            st.success(f"Product '{pf_name_val}' updated!")
                            fetch_products.clear()
                            st.session_state.selected_edit_product = None
                            st.rerun()
                        else:
                            st.error(f"Update failed: {r.text}")
                    else:
                        r = api_post("/products", json=payload, timeout=10.0)
                        if r.status_code == 201:
                            st.success(f"Product '{pf_name_val}' created!")
                            fetch_products.clear()
                            st.rerun()
                        else:
                            st.error(f"Creation failed: {r.text}")
            except ValueError:
                st.error("Invalid price values. Please enter valid numbers.")
            except Exception as exc:
                st.error(f"API error: {exc}")

# ---------------------------------------------------------------------------
# Recent ingestion history for selected product
# ---------------------------------------------------------------------------
if selected_product:
    st.divider()
    with st.expander("Recent Ingestion History"):
        runs_data = fetch_ingestion_runs(product_id=selected_product["product_id"], page_size=10)
        runs = runs_data.get("runs", [])
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
            )
        else:
            st.info("No ingestion runs found for this product.")

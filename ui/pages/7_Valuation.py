"""Manual verified references and listing valuation."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

import httpx
import pandas as pd
import streamlit as st

from ui.lib.api import api_delete, api_get, api_post, fetch_products
from ui.lib.config import get_api_url


def _format_eur(value: object) -> str:
    if value is None:
        return "Unknown"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "Unknown"
    if not amount.is_finite():
        return "Unknown"
    return f"€{amount:,.2f}"


st.markdown("# Verified Valuation")
st.caption("Maintain auditable sold-price references and calculate a net maximum buy price.")

products = fetch_products()
if not products:
    st.warning("Create a product before adding valuation references.")
    st.stop()

product_options = {product["name"]: product["product_id"] for product in products}
selected_product_name = st.selectbox("Product", options=list(product_options))
selected_product_id = product_options[selected_product_name]

try:
    response = api_get(
        "/valuation/references",
        params={"product_id": selected_product_id, "include_inactive": True},
    )
    response.raise_for_status()
    references = response.json()
except Exception as exc:
    st.error(f"Could not load valuation references: {exc}")
    references = []

st.subheader("Reference history")
if references:
    reference_rows = pd.DataFrame(references)
    display_columns = [
        "purchase_source",
        "destination_marketplace",
        "condition",
        "reviewed_price_eur",
        "reviewed_at",
        "expires_at",
        "reviewed_by",
        "is_active",
    ]
    st.dataframe(reference_rows[display_columns], hide_index=True, use_container_width=True)
    active_references = [reference for reference in references if reference["is_active"]]
    if active_references:
        labels = {
            (
                f"{reference['purchase_source']} → {reference['destination_marketplace']} · "
                f"{reference['condition']} · €{reference['reviewed_price_eur']} · "
                f"{reference['reference_id'][:8]}"
            ): reference["reference_id"]
            for reference in active_references
        }
        selected_reference = st.selectbox("Active reference", list(labels))
        if st.button("Deactivate selected reference"):
            delete_response = api_delete(f"/valuation/references/{labels[selected_reference]}")
            if delete_response.status_code == 204:
                st.success("Reference deactivated; its audit record was preserved.")
                st.rerun()
            else:
                st.error(delete_response.text)
else:
    st.info("No verified reference exists for this product.")

st.divider()
st.subheader("Add a verified reference")
st.info(
    "Enter zero explicitly when a fee or reserve does not apply. Rates are decimal values: "
    "0.10 means 10%."
)

today = datetime.now(UTC).date()
with st.form("create_valuation_reference", clear_on_submit=True):
    source_col, destination_col, condition_col = st.columns(3)
    with source_col:
        purchase_source = st.selectbox("Purchase source", ["leboncoin", "ebay", "vinted"])
    with destination_col:
        destination = st.selectbox("Destination marketplace", ["ebay", "leboncoin", "vinted"])
    with condition_col:
        condition = st.selectbox("Comparable condition", ["new", "like_new", "good", "fair"])

    required_text = st.text_input("Required title tokens (comma-separated)")
    excluded_text = st.text_input("Excluded title tokens (comma-separated)")
    reviewed_price = st.number_input(
        "Reviewed comparable sale price (€)", min_value=0.01, step=1.0, format="%.2f"
    )
    evidence_url = st.text_input("Evidence URL")
    date_col1, date_col2, date_col3 = st.columns(3)
    with date_col1:
        comparable_sold_date = st.date_input("Comparable sold date", value=today)
    with date_col2:
        reviewed_date = st.date_input("Reviewed date", value=today)
    with date_col3:
        expires_date = st.date_input("Expires date", value=today + timedelta(days=30))
    reviewed_by = st.text_input("Reviewer")
    limitations = st.text_area("Evidence limitations")

    st.markdown("#### Explicit costs and rates")
    cost_col1, cost_col2, cost_col3 = st.columns(3)
    with cost_col1:
        purchase_rate = st.number_input(
            "Purchase fee rate", min_value=0.0, max_value=1.0, step=0.001, format="%.4f"
        )
        purchase_fixed = st.number_input(
            "Purchase fixed fee (€)", min_value=0.0, step=0.5, format="%.2f"
        )
        outbound_logistics = st.number_input(
            "Outbound logistics (€)", min_value=0.0, step=1.0, format="%.2f"
        )
    with cost_col2:
        sell_rate = st.number_input(
            "Sell fee rate", min_value=0.0, max_value=1.0, step=0.001, format="%.4f"
        )
        sell_fixed = st.number_input("Sell fixed fee (€)", min_value=0.0, step=0.5, format="%.2f")
        risk_allowance = st.number_input(
            "Risk allowance (€)", min_value=0.0, step=1.0, format="%.2f"
        )
    with cost_col3:
        social_rate = st.number_input(
            "Social charge rate", min_value=0.0, max_value=1.0, step=0.001, format="%.4f"
        )
        minimum_contribution = st.number_input(
            "Minimum contribution (€)", min_value=0.0, step=1.0, format="%.2f"
        )

    submitted = st.form_submit_button("Save verified reference", type="primary")
    if submitted:
        required_tokens = [token.strip() for token in required_text.split(",") if token.strip()]
        excluded_tokens = [token.strip() for token in excluded_text.split(",") if token.strip()]
        payload = {
            "product_id": selected_product_id,
            "purchase_source": purchase_source,
            "destination_marketplace": destination,
            "required_tokens": required_tokens,
            "excluded_tokens": excluded_tokens,
            "condition": condition,
            "reviewed_price_eur": reviewed_price,
            "currency": "EUR",
            "evidence_url": evidence_url.strip(),
            "comparable_sold_at": datetime.combine(
                comparable_sold_date, time.min, tzinfo=UTC
            ).isoformat(),
            "reviewed_at": datetime.combine(reviewed_date, time.min, tzinfo=UTC).isoformat(),
            "reviewed_by": reviewed_by.strip(),
            "expires_at": datetime.combine(expires_date, time.min, tzinfo=UTC).isoformat(),
            "limitations": limitations.strip(),
            "purchase_fee_rate": purchase_rate,
            "purchase_fee_fixed_eur": purchase_fixed,
            "sell_fee_rate": sell_rate,
            "sell_fee_fixed_eur": sell_fixed,
            "social_charge_rate": social_rate,
            "outbound_logistics_eur": outbound_logistics,
            "risk_allowance_eur": risk_allowance,
            "minimum_contribution_eur": minimum_contribution,
        }
        create_response = api_post("/valuation/references", json=payload)
        if create_response.status_code == 201:
            st.success("Verified reference saved.")
            st.rerun()
        else:
            st.error(create_response.text)

st.divider()
st.subheader("Review and evaluate a current listing")
obs_id = st.number_input("Observation ID", min_value=1, step=1)
if st.button("Load listing for review"):
    detail_response = api_get(f"/listings/{int(obs_id)}/detail")
    if detail_response.status_code == 200:
        st.session_state.valuation_review_listing = detail_response.json()["observation"]
    else:
        st.error(detail_response.text)

review_listing = st.session_state.get("valuation_review_listing")
if review_listing and review_listing["obs_id"] == int(obs_id):
    st.write(
        {
            "raw_title": review_listing["title"],
            "raw_price_eur": review_listing["price"],
            "raw_condition": review_listing["condition"],
            "raw_shipping_cost_eur": review_listing["shipping_cost"],
            "last_seen_at": review_listing["last_seen_at"],
            "listing_url": review_listing["url"],
        }
    )
    if review_listing["url"]:
        st.link_button("Open marketplace listing", review_listing["url"])
    st.caption(
        "This review is tied to the raw title, price, condition, and shipping shown above. "
        "Any subsequent change invalidates it."
    )
    with st.form("review_listing_inputs"):
        reviewed_title = st.text_input("Verified exact-model title", value=review_listing["title"])
        reviewed_condition = st.selectbox("Verified condition", ["new", "like_new", "good", "fair"])
        reviewed_shipping = st.number_input(
            "Verified inbound shipping (€)",
            min_value=0.0,
            value=float(review_listing["shipping_cost"] or 0),
            step=1.0,
            format="%.2f",
        )
        listing_reviewer = st.text_input("Reviewer", key="listing_review_reviewer")
        review_notes = st.text_area(
            "Review evidence notes",
            help="Record how identity, condition, and shipping were verified.",
        )
        review_valid_hours = st.number_input(
            "Review validity (hours)", min_value=1, max_value=24, value=2
        )
        if st.form_submit_button("Save listing review"):
            reviewed_at = datetime.now(UTC)
            review_response = httpx.patch(
                f"{get_api_url()}/valuation/listings/{int(obs_id)}/review",
                json={
                    "reviewed_title": reviewed_title,
                    "reviewed_condition": reviewed_condition,
                    "reviewed_shipping_cost_eur": reviewed_shipping,
                    "reviewed_by": listing_reviewer,
                    "reviewed_at": reviewed_at.isoformat(),
                    "expires_at": (
                        reviewed_at + timedelta(hours=int(review_valid_hours))
                    ).isoformat(),
                    "notes": review_notes,
                },
                timeout=15.0,
            )
            if review_response.status_code == 201:
                st.success("Listing inputs reviewed. Evaluate before the review expires.")
            else:
                st.error(review_response.text)

if st.button("Evaluate listing", type="primary"):
    valuation_response = api_get(f"/valuation/listings/{int(obs_id)}")
    if valuation_response.status_code != 200:
        st.error(valuation_response.text)
    else:
        valuation = valuation_response.json()
        if valuation["eligible"]:
            st.success("Eligible under the selected verified reference.")
        else:
            st.warning("Blocked: " + ", ".join(valuation["reasons"]))
        metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
        metric_col1.metric("Exit estimate", _format_eur(valuation["estimated_sale_price_eur"]))
        metric_col2.metric("Acquisition cost", _format_eur(valuation["acquisition_cost_eur"]))
        metric_col3.metric("Contribution", _format_eur(valuation["contribution_eur"]))
        metric_col4.metric("Maximum buy", _format_eur(valuation["max_buy_price_eur"]))
        st.write("Cost breakdown", valuation.get("cost_breakdown"))
        st.write("Reference snapshot", valuation.get("reference_snapshot"))

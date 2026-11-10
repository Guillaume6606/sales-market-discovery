"""Review eBay.fr sold-comparable proposals before they become valuation references."""

from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from ui.lib.api import api_get, api_post, fetch_products

STATUSES = ["pending", "insufficient", "approved", "rejected"]

st.markdown("# Sold Comparables")
st.caption(
    "Proposals summarize eBay.fr completed sales. They never authorize an alert: only an "
    "approved proposal creates a verified valuation reference."
)

products = fetch_products()
product_names = {product["product_id"]: product["name"] for product in products}

status_choice = st.selectbox("Status", STATUSES)
try:
    response = api_get("/sold-comps/proposals", params={"status": status_choice})
    response.raise_for_status()
    proposals = response.json()
except Exception as exc:
    st.error(f"Could not load proposals: {exc}")
    proposals = []

if not proposals:
    st.info(f"No {status_choice} proposal.")
    st.stop()

rows = pd.DataFrame(
    [
        {
            "product": product_names.get(proposal["product_id"], proposal["product_id"]),
            "condition": proposal["condition"],
            "sales": f"{proposal['sale_count']} / min {proposal['min_comparables']}",
            "median_eur": proposal["median_price_eur"],
            "p25_eur": proposal["p25_price_eur"],
            "p75_eur": proposal["p75_price_eur"],
            "sold_from": proposal["first_sold_at"],
            "sold_to": proposal["last_sold_at"],
            "computed_at": proposal["computed_at"],
        }
        for proposal in proposals
    ]
)
st.dataframe(rows, hide_index=True, use_container_width=True)

labels = {
    (
        f"{product_names.get(proposal['product_id'], proposal['product_id'])} · "
        f"{proposal['condition']} · {proposal['proposal_id'][:8]}"
    ): proposal["proposal_id"]
    for proposal in proposals
}
selected_label = st.selectbox("Proposal", list(labels))
detail_response = api_get(f"/sold-comps/proposals/{labels[selected_label]}")
if detail_response.status_code != 200:
    st.error(detail_response.text)
    st.stop()
proposal = detail_response.json()

st.markdown(
    f"**Median item price (excluding shipping):** €{proposal['median_price_eur']} · "
    f"IQR €{proposal['p25_price_eur']}–€{proposal['p75_price_eur']} · "
    f"{proposal['sale_count']} sales"
)
st.write(
    {
        "required_tokens": proposal["required_tokens"],
        "excluded_tokens": proposal["excluded_tokens"],
        "search_query": proposal["search_query"],
    }
)
st.link_button("Open eBay.fr sold search", proposal["search_url"])

st.subheader("Source sales")
if proposal["sales"]:
    st.dataframe(pd.DataFrame(proposal["sales"]), hide_index=True, use_container_width=True)
with st.expander(f"Excluded sales ({len(proposal['excluded_sales'])})"):
    if proposal["excluded_sales"]:
        st.dataframe(
            pd.DataFrame(proposal["excluded_sales"]), hide_index=True, use_container_width=True
        )

if proposal["status"] == "insufficient":
    st.warning(
        "Insufficient evidence: fewer comparables than the configured minimum. "
        "This proposal cannot be approved."
    )

if proposal["status"] == "pending":
    st.subheader("Approve")
    st.info(
        "Approval creates a verified reference at the median price. Enter every cost explicitly; "
        "zero is an operator assumption. Rates are decimals: 0.10 means 10%."
    )
    with st.form("approve_sold_comp"):
        source_col, destination_col = st.columns(2)
        with source_col:
            purchase_source = st.selectbox(
                "Purchase source", ["leboncoin", "ebay", "vinted", "cashconverters"]
            )
        with destination_col:
            destination = st.selectbox("Destination marketplace", ["ebay", "leboncoin", "vinted"])
        expires_date = st.date_input(
            "Reference expires", value=datetime.now(UTC).date() + timedelta(days=30)
        )
        reviewed_by = st.text_input("Reviewer")
        limitations = st.text_area("Evidence limitations")
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
            sell_fixed = st.number_input(
                "Sell fixed fee (€)", min_value=0.0, step=0.5, format="%.2f"
            )
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
        if st.form_submit_button("Approve and create reference", type="primary"):
            approve_response = api_post(
                f"/sold-comps/proposals/{proposal['proposal_id']}/approve",
                json={
                    "purchase_source": purchase_source,
                    "destination_marketplace": destination,
                    "reviewed_by": reviewed_by.strip(),
                    "expires_at": datetime.combine(
                        expires_date, datetime.min.time(), tzinfo=UTC
                    ).isoformat(),
                    "limitations": limitations.strip(),
                    "purchase_fee_rate": purchase_rate,
                    "purchase_fee_fixed_eur": purchase_fixed,
                    "sell_fee_rate": sell_rate,
                    "sell_fee_fixed_eur": sell_fixed,
                    "social_charge_rate": social_rate,
                    "outbound_logistics_eur": outbound_logistics,
                    "risk_allowance_eur": risk_allowance,
                    "minimum_contribution_eur": minimum_contribution,
                },
            )
            if approve_response.status_code == 201:
                st.success("Approved: a verified valuation reference was created.")
            else:
                st.error(approve_response.text)

if proposal["status"] in {"pending", "insufficient"}:
    st.subheader("Reject")
    with st.form("reject_sold_comp"):
        reject_by = st.text_input("Reviewer", key="reject_reviewer")
        reject_note = st.text_area("Reason")
        if st.form_submit_button("Reject proposal"):
            reject_response = api_post(
                f"/sold-comps/proposals/{proposal['proposal_id']}/reject",
                json={"reviewed_by": reject_by.strip(), "note": reject_note.strip()},
            )
            if reject_response.status_code == 200:
                st.success("Proposal rejected.")
            else:
                st.error(reject_response.text)

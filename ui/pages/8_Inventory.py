"""Private operator inventory, settlement, and realized P&L ledger."""

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

from ui.lib.api import api_get, api_post, api_put

OPEN_STATUSES = {"purchased", "listed", "sold"}
STATUS_LABELS = {
    "purchased": "Purchased",
    "listed": "Listed",
    "sold": "Sold, payout pending",
    "settled": "Settled and final",
    "returned": "Returned to supplier (closed)",
    "written_off": "Written off (closed)",
}
TRANSITIONS = {
    "purchased": ["purchased", "listed", "sold", "returned", "written_off"],
    "listed": ["listed", "sold", "returned", "written_off"],
    "sold": ["sold", "settled", "returned", "written_off"],
    "settled": ["settled"],
    "returned": ["returned"],
    "written_off": ["written_off"],
}
COST_FIELDS = [
    ("acquisition_price_eur", "Purchase price"),
    ("acquisition_fees_eur", "Acquisition fees"),
    ("inbound_logistics_eur", "Inbound logistics"),
    ("repair_cost_eur", "Repairs"),
    ("selling_fees_eur", "Selling/payment fees"),
    ("outbound_logistics_eur", "Outbound logistics"),
    ("refund_cost_eur", "Customer refund / recovery costs"),
    ("turnover_charges_eur", "Turnover charges"),
]


def _amount(value: Any) -> float:
    return float(value or 0)


def _load_trades() -> list[dict[str, Any]]:
    try:
        response = api_get("/trades", params={"limit": 500})
        response.raise_for_status()
        return response.json().get("trades", [])
    except Exception as exc:
        st.error(f"Could not load the trade ledger: {exc}")
        return []


def _load_summary(month: date) -> dict[str, Any] | None:
    try:
        response = api_get("/trades/summary/monthly", params={"month": month.isoformat()})
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        st.error(f"Could not load the monthly summary: {exc}")
        return None


def _optional_positive_int(raw_value: str) -> int | None:
    raw_value = raw_value.strip()
    if not raw_value:
        return None
    value = int(raw_value)
    if value <= 0:
        raise ValueError("Observation ID must be a positive integer")
    return value


st.markdown("# Inventory & Realized P&L")
st.caption("Actual cash outcomes, inventory aging, committed capital, and monthly overhead")
st.info(
    "Mark a trade settled only after payout and its return window are final. "
    "Returned means returned to the supplier and closed; customer-returned stock "
    "intended for resale remains open."
)

if st.button("Refresh", key="trade_refresh"):
    st.rerun()

selected_day = st.date_input("Reporting month", value=date.today().replace(day=1))
selected_month = selected_day.replace(day=1)
trades = _load_trades()
summary = _load_summary(selected_month)

if summary:
    columns = st.columns(5)
    columns[0].metric("Operating profit", f"€{_amount(summary['operating_profit_eur']):,.2f}")
    columns[1].metric("Trade profit", f"€{_amount(summary['realized_trade_profit_eur']):,.2f}")
    columns[2].metric("Revenue received", f"€{_amount(summary['realized_revenue_eur']):,.2f}")
    columns[3].metric(
        "Current capital committed", f"€{_amount(summary['inventory_capital_eur']):,.2f}"
    )
    columns[4].metric("Aged inventory", int(summary["aged_inventory_count"]))
    st.caption(
        f"{summary['closed_trade_count']} closed trade(s) · "
        f"{summary['inventory_count']} open item(s) · "
        f"€{_amount(summary['monthly_overhead_eur']):,.2f} monthly overhead · "
        f"inventory as of {summary.get('inventory_as_of', date.today().isoformat())}"
    )

purchase_tab, update_tab, overhead_tab = st.tabs(
    ["Record purchase", "Update / settle", "Monthly overhead"]
)

with purchase_tab:
    with st.form("record_purchase", clear_on_submit=True):
        title = st.text_input("Product / listing title *")
        p1, p2, p3 = st.columns(3)
        with p1:
            buy_platform = st.text_input("Buy platform *", value="leboncoin")
        with p2:
            exit_platform = st.text_input("Planned exit platform *", value="ebay")
        with p3:
            observation_id = st.text_input("Observation ID (optional)")
        acquired_on = st.date_input("Acquired on", value=date.today(), key="new_acquired")
        c1, c2, c3 = st.columns(3)
        with c1:
            acquisition_price = st.number_input("Purchase price (€) *", min_value=0.0, step=1.0)
        with c2:
            acquisition_fees = st.number_input("Acquisition fees (€)", min_value=0.0, step=1.0)
        with c3:
            inbound_logistics = st.number_input("Inbound logistics (€)", min_value=0.0, step=1.0)
        f1, f2 = st.columns(2)
        with f1:
            forecast_sale = st.number_input("Forecast sale value (€)", min_value=0.0, step=1.0)
        with f2:
            forecast_costs = st.number_input(
                "Forecast remaining costs (€)", min_value=0.0, step=1.0
            )
        notes = st.text_area("Notes")

        if st.form_submit_button("Record purchase", type="primary"):
            try:
                payload = {
                    "title": title.strip(),
                    "buy_platform": buy_platform.strip(),
                    "exit_platform": exit_platform.strip(),
                    "observation_id": _optional_positive_int(observation_id),
                    "acquired_on": acquired_on.isoformat(),
                    "acquisition_price_eur": acquisition_price,
                    "acquisition_fees_eur": acquisition_fees,
                    "inbound_logistics_eur": inbound_logistics,
                    "forecast_sale_revenue_eur": forecast_sale or None,
                    "forecast_remaining_costs_eur": forecast_costs or None,
                    "notes": notes or None,
                }
                response = api_post("/trades", json=payload)
                if response.status_code == 201:
                    st.success("Purchase recorded")
                    st.rerun()
                else:
                    st.error(response.text)
            except ValueError as exc:
                st.error(str(exc))

with update_tab:
    if not trades:
        st.info("Record a purchase to start the ledger.")
    else:
        labels = {
            f"{trade['title']} · {trade['status']} · {str(trade['trade_id'])[:8]}": trade
            for trade in trades
        }
        selected_label = st.selectbox("Trade", list(labels))
        selected_trade = labels[selected_label]
        current_status = selected_trade["status"]
        new_status = st.selectbox(
            "New status",
            TRANSITIONS[current_status],
            format_func=STATUS_LABELS.get,
            key=f"trade_status_{selected_trade['trade_id']}",
        )

        with st.form(f"edit_trade_{selected_trade['trade_id']}"):
            edit_title = st.text_input("Product / listing title", value=selected_trade["title"])
            e1, e2 = st.columns(2)
            with e1:
                edit_buy_platform = st.text_input(
                    "Buy platform", value=selected_trade["buy_platform"]
                )
            with e2:
                edit_exit_platform = st.text_input(
                    "Exit platform", value=selected_trade["exit_platform"]
                )
            edit_acquired_on = st.date_input(
                "Acquired on", value=date.fromisoformat(selected_trade["acquired_on"])
            )

            amounts: dict[str, float] = {}
            left, right = st.columns(2)
            for index, (field, label) in enumerate(COST_FIELDS):
                column = left if index % 2 == 0 else right
                with column:
                    amounts[field] = st.number_input(
                        f"{label} (€)",
                        min_value=0.0,
                        value=_amount(selected_trade.get(field)),
                        step=1.0,
                        key=f"{field}_{selected_trade['trade_id']}",
                    )

            sold_on = (
                date.fromisoformat(selected_trade["sold_on"])
                if selected_trade.get("sold_on")
                else None
            )
            settled_on = None
            closed_on = None
            sale_revenue = None
            if new_status in {"sold", "settled"}:
                sold_on = st.date_input(
                    "Sold on",
                    value=date.fromisoformat(selected_trade["sold_on"])
                    if selected_trade.get("sold_on")
                    else date.today(),
                )
            if new_status == "settled":
                settled_on = st.date_input(
                    "Final payout / return-window close date",
                    value=date.fromisoformat(selected_trade["settled_on"])
                    if selected_trade.get("settled_on")
                    else date.today(),
                )
            if new_status in {"returned", "written_off"}:
                closed_on = st.date_input(
                    "Closed on",
                    value=date.fromisoformat(selected_trade["closed_on"])
                    if selected_trade.get("closed_on")
                    else date.today(),
                )
            if new_status in {"sold", "settled", "returned", "written_off"}:
                sale_revenue = st.number_input(
                    "Actual proceeds (€; enter 0 for a total loss)",
                    min_value=0.0,
                    value=_amount(selected_trade.get("sale_revenue_eur")),
                    step=1.0,
                )

            fc1, fc2 = st.columns(2)
            with fc1:
                edit_forecast_sale = st.number_input(
                    "Forecast sale value (€)",
                    min_value=0.0,
                    value=_amount(selected_trade.get("forecast_sale_revenue_eur")),
                    step=1.0,
                )
            with fc2:
                edit_forecast_costs = st.number_input(
                    "Forecast remaining costs (€)",
                    min_value=0.0,
                    value=_amount(selected_trade.get("forecast_remaining_costs_eur")),
                    step=1.0,
                )
            minutes = st.number_input(
                "Time spent (minutes)",
                min_value=0,
                value=int(selected_trade.get("time_spent_minutes") or 0),
                step=5,
            )
            edit_notes = st.text_area("Notes", value=selected_trade.get("notes") or "")

            if st.form_submit_button("Save trade", type="primary"):
                payload = {
                    "title": edit_title.strip(),
                    "buy_platform": edit_buy_platform.strip(),
                    "exit_platform": edit_exit_platform.strip(),
                    "status": new_status,
                    "acquired_on": edit_acquired_on.isoformat(),
                    "sold_on": sold_on.isoformat() if sold_on else None,
                    "settled_on": settled_on.isoformat() if settled_on else None,
                    "closed_on": closed_on.isoformat() if closed_on else None,
                    "sale_revenue_eur": sale_revenue,
                    "forecast_sale_revenue_eur": edit_forecast_sale or None,
                    "forecast_remaining_costs_eur": edit_forecast_costs or None,
                    "time_spent_minutes": minutes,
                    "notes": edit_notes or None,
                    **amounts,
                }
                response = api_put(f"/trades/{selected_trade['trade_id']}", json=payload)
                if response.status_code == 200:
                    st.success("Trade updated")
                    st.rerun()
                else:
                    st.error(response.text)

with overhead_tab:
    with st.form("monthly_overhead"):
        overhead_amount = st.number_input(
            "Monthly overhead (€)",
            min_value=0.0,
            value=_amount(summary.get("monthly_overhead_eur")) if summary else 0.0,
            step=5.0,
        )
        overhead_notes = st.text_input("Notes", placeholder="Tools, subscriptions, storage…")
        if st.form_submit_button("Save monthly overhead"):
            response = api_put(
                "/trades/overheads/monthly",
                json={
                    "month": selected_month.isoformat(),
                    "amount_eur": overhead_amount,
                    "notes": overhead_notes or None,
                },
            )
            if response.status_code == 200:
                st.success("Monthly overhead saved once for this month")
                st.rerun()
            else:
                st.error(response.text)

st.divider()
st.subheader("Inventory")
inventory = [trade for trade in trades if trade["status"] in OPEN_STATUSES]
if inventory:
    inventory_frame = pd.DataFrame(inventory)
    inventory_columns = [
        "title",
        "status",
        "buy_platform",
        "exit_platform",
        "acquired_on",
        "age_days",
        "inventory_capital_eur",
        "forecast_profit_eur",
    ]
    st.dataframe(inventory_frame[inventory_columns], hide_index=True, use_container_width=True)
else:
    st.info("No open inventory.")

st.subheader("All trades")
if trades:
    trades_frame = pd.DataFrame(trades)
    display_columns = [
        "title",
        "status",
        "acquired_on",
        "sold_on",
        "settled_on",
        "closed_on",
        "recognized_revenue_eur",
        "actual_costs_eur",
        "realized_profit_eur",
    ]
    st.dataframe(trades_frame[display_columns], hide_index=True, use_container_width=True)
    try:
        csv_response = api_get("/trades/export.csv")
        csv_response.raise_for_status()
        st.download_button(
            "Download full ledger CSV",
            data=csv_response.content,
            file_name="trade-ledger.csv",
            mime="text/csv",
        )
    except Exception as exc:
        st.warning(f"Full CSV export is unavailable: {exc}")
else:
    st.info("No trades recorded yet.")

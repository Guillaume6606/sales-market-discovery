from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from libs.common.trades import (
    calculate_trade_financials,
    capital_committed,
    summarize_month,
    validate_status_transition,
    validate_trade_lifecycle,
)


def _trade(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "status": "purchased",
        "acquired_on": date(2026, 7, 1),
        "sold_on": None,
        "settled_on": None,
        "closed_on": None,
        "acquisition_price_eur": Decimal("300.00"),
        "acquisition_fees_eur": Decimal("5.00"),
        "inbound_logistics_eur": Decimal("10.00"),
        "repair_cost_eur": Decimal("15.00"),
        "selling_fees_eur": Decimal("0.00"),
        "outbound_logistics_eur": Decimal("0.00"),
        "refund_cost_eur": Decimal("0.00"),
        "turnover_charges_eur": Decimal("0.00"),
        "sale_revenue_eur": None,
        "forecast_sale_revenue_eur": Decimal("450.00"),
        "forecast_remaining_costs_eur": Decimal("55.00"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_unclosed_sale_has_no_recognized_revenue_and_remains_inventory() -> None:
    trade = _trade(
        status="sold",
        sold_on=date(2026, 7, 20),
        sale_revenue_eur=Decimal("500.00"),
        selling_fees_eur=Decimal("50.00"),
        outbound_logistics_eur=Decimal("12.00"),
    )

    result = calculate_trade_financials(trade, as_of=date(2026, 8, 1))

    assert result["recognized_revenue_eur"] == Decimal("0.00")
    assert result["realized_profit_eur"] is None
    assert result["inventory_capital_eur"] == Decimal("392.00")
    assert result["age_days"] == 31


def test_settled_trade_profit_uses_actual_costs_without_forecast_reserves() -> None:
    trade = _trade(
        status="settled",
        sold_on=date(2026, 7, 20),
        settled_on=date(2026, 8, 2),
        sale_revenue_eur=Decimal("500.00"),
        selling_fees_eur=Decimal("50.00"),
        outbound_logistics_eur=Decimal("12.00"),
        refund_cost_eur=Decimal("8.00"),
        turnover_charges_eur=Decimal("61.50"),
        forecast_remaining_costs_eur=Decimal("999.00"),
    )

    result = calculate_trade_financials(trade, as_of=date(2026, 8, 31))

    assert result["actual_costs_eur"] == Decimal("461.50")
    assert result["recognized_revenue_eur"] == Decimal("500.00")
    assert result["realized_profit_eur"] == Decimal("38.50")
    assert result["inventory_capital_eur"] == Decimal("0.00")
    assert result["forecast_profit_eur"] is None


@pytest.mark.parametrize("status", ["returned", "written_off"])
def test_exceptional_close_can_record_a_zero_proceeds_loss(status: str) -> None:
    trade = _trade(
        status=status,
        closed_on=date(2026, 8, 3),
        sale_revenue_eur=Decimal("0.00"),
    )

    result = calculate_trade_financials(trade, as_of=date(2026, 8, 31))

    assert result["recognized_revenue_eur"] == Decimal("0.00")
    assert result["realized_profit_eur"] == Decimal("-330.00")
    assert result["inventory_capital_eur"] == Decimal("0.00")


def test_month_summary_counts_overhead_once_and_exposes_current_inventory() -> None:
    settled = _trade(
        status="settled",
        sold_on=date(2026, 7, 25),
        settled_on=date(2026, 8, 4),
        sale_revenue_eur=Decimal("500.00"),
    )
    written_off = _trade(
        status="written_off",
        closed_on=date(2026, 8, 10),
        sale_revenue_eur=Decimal("0.00"),
        acquisition_price_eur=Decimal("100.00"),
        acquisition_fees_eur=Decimal("0.00"),
        inbound_logistics_eur=Decimal("0.00"),
        repair_cost_eur=Decimal("0.00"),
    )
    inventory = _trade(acquired_on=date(2026, 6, 15))
    overhead = SimpleNamespace(month=date(2026, 8, 1), amount_eur=Decimal("150.00"))

    result = summarize_month(
        [settled, written_off, inventory],
        [overhead],
        month=date(2026, 8, 1),
        as_of=date(2026, 8, 31),
    )

    assert result["realized_revenue_eur"] == Decimal("500.00")
    assert result["realized_trade_profit_eur"] == Decimal("70.00")
    assert result["monthly_overhead_eur"] == Decimal("150.00")
    assert result["operating_profit_eur"] == Decimal("-80.00")
    assert result["closed_trade_count"] == 2
    assert result["inventory_count"] == 1
    assert result["inventory_capital_eur"] == Decimal("330.00")
    assert result["aged_inventory_count"] == 1
    assert result["inventory_as_of"] == date(2026, 8, 31)


def test_month_summary_rejects_duplicate_overhead_rows() -> None:
    rows = [
        SimpleNamespace(month=date(2026, 8, 1), amount_eur=Decimal("100.00")),
        SimpleNamespace(month=date(2026, 8, 1), amount_eur=Decimal("50.00")),
    ]

    with pytest.raises(ValueError, match="only one overhead row"):
        summarize_month([], rows, month=date(2026, 8, 1), as_of=date(2026, 8, 31))


def test_zero_cost_item_still_counts_as_inventory() -> None:
    gift = _trade(
        acquisition_price_eur=Decimal("0.00"),
        acquisition_fees_eur=Decimal("0.00"),
        inbound_logistics_eur=Decimal("0.00"),
        repair_cost_eur=Decimal("0.00"),
    )

    result = summarize_month(
        [gift],
        [],
        month=date(2026, 8, 1),
        as_of=date(2026, 8, 31),
    )

    assert result["inventory_count"] == 1
    assert result["inventory_capital_eur"] == Decimal("0.00")


def test_capital_committed_excludes_every_terminal_status() -> None:
    inventory = _trade()
    settled = _trade(
        status="settled",
        sold_on=date(2026, 8, 1),
        settled_on=date(2026, 8, 2),
        sale_revenue_eur=Decimal("400.00"),
    )
    returned = _trade(
        status="returned",
        closed_on=date(2026, 8, 3),
        sale_revenue_eur=Decimal("0.00"),
    )
    written_off = _trade(
        status="written_off",
        closed_on=date(2026, 8, 4),
        sale_revenue_eur=Decimal("0.00"),
    )

    result = capital_committed([inventory, settled, returned, written_off], as_of=date(2026, 8, 31))

    assert result == Decimal("330.00")


def test_lifecycle_requires_chronological_settlement_dates() -> None:
    with pytest.raises(ValueError, match="settled_on cannot precede sold_on"):
        validate_trade_lifecycle(
            status="settled",
            acquired_on=date(2026, 8, 1),
            sold_on=date(2026, 8, 10),
            settled_on=date(2026, 8, 9),
            closed_on=None,
            sale_revenue_eur=Decimal("500.00"),
        )


def test_terminal_trade_cannot_be_reopened() -> None:
    with pytest.raises(ValueError, match="terminal trade cannot change status"):
        validate_status_transition("written_off", "purchased")

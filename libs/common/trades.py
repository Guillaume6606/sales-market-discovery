from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from libs.common.trade_models import TERMINAL_TRADE_STATUSES, Trade

ZERO = Decimal("0.00")
ACTUAL_COST_FIELDS = (
    "acquisition_price_eur",
    "acquisition_fees_eur",
    "inbound_logistics_eur",
    "repair_cost_eur",
    "selling_fees_eur",
    "outbound_logistics_eur",
    "refund_cost_eur",
    "turnover_charges_eur",
)


def _decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite():
        raise ValueError("money amounts must be finite")
    return result


def trade_close_date(trade: Any) -> date | None:
    if trade.status == "settled":
        return trade.settled_on
    if trade.status in {"returned", "written_off"}:
        return trade.closed_on
    return None


def calculate_trade_financials(trade: Any, *, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    actual_costs = sum(
        (_decimal(getattr(trade, field, None)) for field in ACTUAL_COST_FIELDS), ZERO
    )
    close_date = trade_close_date(trade)
    is_closed = (
        trade.status in TERMINAL_TRADE_STATUSES and close_date is not None and close_date <= as_of
    )
    recognized_revenue = _decimal(trade.sale_revenue_eur) if is_closed else ZERO
    realized_profit = recognized_revenue - actual_costs if is_closed else None
    is_inventory = trade.acquired_on <= as_of and not is_closed
    inventory_capital = actual_costs if is_inventory else ZERO

    forecast_profit: Decimal | None = None
    forecast_revenue = getattr(trade, "forecast_sale_revenue_eur", None)
    if is_inventory and forecast_revenue is not None:
        forecast_profit = (
            _decimal(forecast_revenue)
            - actual_costs
            - _decimal(getattr(trade, "forecast_remaining_costs_eur", None))
        )

    return {
        "actual_costs_eur": actual_costs,
        "recognized_revenue_eur": recognized_revenue,
        "realized_profit_eur": realized_profit,
        "inventory_capital_eur": inventory_capital,
        "is_inventory": is_inventory,
        "forecast_profit_eur": forecast_profit,
        "age_days": max((as_of - trade.acquired_on).days, 0),
        "close_date": close_date,
    }


def capital_committed(trades: Iterable[Any], *, as_of: date | None = None) -> Decimal:
    as_of = as_of or date.today()
    return sum(
        (
            calculate_trade_financials(trade, as_of=as_of)["inventory_capital_eur"]
            for trade in trades
        ),
        ZERO,
    )


def query_capital_committed(db: Session, *, as_of: date | None = None) -> Decimal:
    as_of = as_of or date.today()
    trades = db.query(Trade).filter(Trade.acquired_on <= as_of).all()
    return capital_committed(trades, as_of=as_of)


def validate_trade_lifecycle(
    *,
    status: str,
    acquired_on: date,
    sold_on: date | None,
    settled_on: date | None,
    closed_on: date | None,
    sale_revenue_eur: Decimal | None,
    today: date | None = None,
) -> None:
    today = today or date.today()
    if any(
        actual_date is not None and actual_date > today
        for actual_date in (acquired_on, sold_on, settled_on, closed_on)
    ):
        raise ValueError("actual trade dates cannot be in the future")
    if sold_on is not None and sold_on < acquired_on:
        raise ValueError("sold_on cannot precede acquired_on")
    if settled_on is not None and sold_on is not None and settled_on < sold_on:
        raise ValueError("settled_on cannot precede sold_on")
    if closed_on is not None and closed_on < acquired_on:
        raise ValueError("closed_on cannot precede acquired_on")
    if closed_on is not None and sold_on is not None and closed_on < sold_on:
        raise ValueError("closed_on cannot precede sold_on")

    if status in {"purchased", "listed"}:
        if sold_on is not None or settled_on is not None or closed_on is not None:
            raise ValueError(f"{status} trades cannot have sale or close dates")
        if sale_revenue_eur is not None:
            raise ValueError("sale_revenue_eur is only valid for sold or closed trades")
    elif status == "sold":
        if sold_on is None:
            raise ValueError("sold trades require sold_on")
        if settled_on is not None or closed_on is not None:
            raise ValueError("sold trades cannot have settlement or close dates")
    elif status == "settled":
        if sold_on is None or settled_on is None:
            raise ValueError("settled trades require sold_on and settled_on")
        if closed_on is not None:
            raise ValueError("settled trades use settled_on, not closed_on")
        if sale_revenue_eur is None:
            raise ValueError("settled trades require explicit sale_revenue_eur")
    elif status in {"returned", "written_off"}:
        if closed_on is None:
            raise ValueError(f"{status} trades require closed_on")
        if settled_on is not None:
            raise ValueError(f"{status} trades cannot have settled_on")
        if sale_revenue_eur is None:
            raise ValueError(f"{status} trades require explicit sale_revenue_eur, including zero")
    else:
        raise ValueError(f"unsupported trade status: {status}")


def validate_status_transition(current_status: str, new_status: str) -> None:
    if current_status == new_status:
        return
    if current_status in TERMINAL_TRADE_STATUSES:
        raise ValueError("terminal trade cannot change status")
    allowed = {
        "purchased": {"listed", "sold", "returned", "written_off"},
        "listed": {"sold", "returned", "written_off"},
        "sold": {"settled", "returned", "written_off"},
    }
    if new_status not in allowed.get(current_status, set()):
        raise ValueError(f"invalid trade status transition: {current_status} to {new_status}")


def summarize_month(
    trades: Iterable[Any],
    overheads: Iterable[Any],
    *,
    month: date,
    as_of: date | None = None,
    aged_after_days: int = 30,
) -> dict[str, Any]:
    if month.day != 1:
        raise ValueError("month must be the first day of a month")
    as_of = as_of or date.today()
    next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)

    month_overheads = [row for row in overheads if row.month == month]
    if len(month_overheads) > 1:
        raise ValueError("only one overhead row is allowed per month")
    monthly_overhead = _decimal(month_overheads[0].amount_eur) if month_overheads else ZERO

    realized_revenue = ZERO
    realized_trade_profit = ZERO
    closed_count = 0
    inventory_count = 0
    inventory_capital = ZERO
    aged_inventory_count = 0

    for trade in trades:
        financials = calculate_trade_financials(trade, as_of=as_of)
        close_date = financials["close_date"]
        if (
            financials["realized_profit_eur"] is not None
            and close_date is not None
            and month <= close_date < next_month
        ):
            realized_revenue += financials["recognized_revenue_eur"]
            realized_trade_profit += financials["realized_profit_eur"]
            closed_count += 1
        if financials["is_inventory"]:
            inventory_count += 1
            inventory_capital += financials["inventory_capital_eur"]
            if financials["age_days"] >= aged_after_days:
                aged_inventory_count += 1

    return {
        "month": month,
        "realized_revenue_eur": realized_revenue,
        "realized_trade_profit_eur": realized_trade_profit,
        "monthly_overhead_eur": monthly_overhead,
        "operating_profit_eur": realized_trade_profit - monthly_overhead,
        "closed_trade_count": closed_count,
        "inventory_count": inventory_count,
        "inventory_capital_eur": inventory_capital,
        "aged_inventory_count": aged_inventory_count,
        "inventory_as_of": as_of,
    }

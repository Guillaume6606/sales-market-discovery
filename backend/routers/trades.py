import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import desc
from sqlalchemy.orm import Session

from libs.common.db import get_db
from libs.common.models import ListingObservation
from libs.common.trade_models import MonthlyOverhead, Trade
from libs.common.trades import (
    calculate_trade_financials,
    summarize_month,
    validate_status_transition,
    validate_trade_lifecycle,
)

router = APIRouter(prefix="/trades", tags=["trades"])

TradeStatus = Literal["purchased", "listed", "sold", "settled", "returned", "written_off"]
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
MONEY_FIELDS = (
    "acquisition_price_eur",
    "acquisition_fees_eur",
    "inbound_logistics_eur",
    "repair_cost_eur",
    "selling_fees_eur",
    "outbound_logistics_eur",
    "refund_cost_eur",
    "turnover_charges_eur",
    "sale_revenue_eur",
    "forecast_sale_revenue_eur",
    "forecast_remaining_costs_eur",
)
TRADE_INPUT_FIELDS = (
    "observation_id",
    "title",
    "buy_platform",
    "exit_platform",
    "status",
    "acquired_on",
    "sold_on",
    "settled_on",
    "closed_on",
    *MONEY_FIELDS,
    "time_spent_minutes",
    "notes",
)


def _finite_decimal(value: Any) -> Any:
    if value is None:
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("money amounts must be valid decimal numbers") from exc
    if not amount.is_finite():
        raise ValueError("money amounts must be finite")
    return amount


class TradeCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    observation_id: int | None = Field(None, gt=0)
    title: str = Field(min_length=1, max_length=500)
    buy_platform: str = Field(min_length=1, max_length=100)
    exit_platform: str = Field(min_length=1, max_length=100)
    status: TradeStatus = "purchased"
    acquired_on: date
    sold_on: date | None = None
    settled_on: date | None = None
    closed_on: date | None = None

    acquisition_price_eur: Money
    acquisition_fees_eur: Money = Decimal("0.00")
    inbound_logistics_eur: Money = Decimal("0.00")
    repair_cost_eur: Money = Decimal("0.00")
    selling_fees_eur: Money = Decimal("0.00")
    outbound_logistics_eur: Money = Decimal("0.00")
    refund_cost_eur: Money = Decimal("0.00")
    turnover_charges_eur: Money = Decimal("0.00")
    sale_revenue_eur: Money | None = None
    forecast_sale_revenue_eur: Money | None = None
    forecast_remaining_costs_eur: Money | None = None

    time_spent_minutes: int = Field(0, ge=0)
    notes: str | None = Field(None, max_length=5000)

    _validate_money = field_validator(*MONEY_FIELDS, mode="before")(_finite_decimal)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "TradeCreate":
        validate_trade_lifecycle(
            status=self.status,
            acquired_on=self.acquired_on,
            sold_on=self.sold_on,
            settled_on=self.settled_on,
            closed_on=self.closed_on,
            sale_revenue_eur=self.sale_revenue_eur,
        )
        return self


class TradeUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    observation_id: int | None = Field(None, gt=0)
    title: str | None = Field(None, min_length=1, max_length=500)
    buy_platform: str | None = Field(None, min_length=1, max_length=100)
    exit_platform: str | None = Field(None, min_length=1, max_length=100)
    status: TradeStatus | None = None
    acquired_on: date | None = None
    sold_on: date | None = None
    settled_on: date | None = None
    closed_on: date | None = None

    acquisition_price_eur: Money | None = None
    acquisition_fees_eur: Money | None = None
    inbound_logistics_eur: Money | None = None
    repair_cost_eur: Money | None = None
    selling_fees_eur: Money | None = None
    outbound_logistics_eur: Money | None = None
    refund_cost_eur: Money | None = None
    turnover_charges_eur: Money | None = None
    sale_revenue_eur: Money | None = None
    forecast_sale_revenue_eur: Money | None = None
    forecast_remaining_costs_eur: Money | None = None

    time_spent_minutes: int | None = Field(None, ge=0)
    notes: str | None = Field(None, max_length=5000)

    _validate_money = field_validator(*MONEY_FIELDS, mode="before")(_finite_decimal)


class MonthlyOverheadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    month: date
    amount_eur: Money
    notes: str | None = Field(None, max_length=5000)

    _validate_amount = field_validator("amount_eur", mode="before")(_finite_decimal)

    @field_validator("month")
    @classmethod
    def validate_month(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("month must be the first day of the month")
        return value


def _trade_values(trade: Trade) -> dict[str, Any]:
    return {field: getattr(trade, field) for field in TRADE_INPUT_FIELDS}


def _serialize_trade(trade: Trade, *, as_of: date | None = None) -> dict[str, Any]:
    values = {
        "trade_id": trade.trade_id,
        **_trade_values(trade),
        "created_at": trade.created_at,
        "updated_at": trade.updated_at,
    }
    values.update(calculate_trade_financials(trade, as_of=as_of))
    return values


def _first_of_month(value: date | None) -> date:
    value = value or date.today().replace(day=1)
    if value.day != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="month must be the first day of a month",
        )
    return value


@router.post("", status_code=status.HTTP_201_CREATED)
def create_trade(payload: TradeCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    if (
        payload.observation_id is not None
        and db.get(ListingObservation, payload.observation_id) is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Listing observation not found")
    trade = Trade(**payload.model_dump())
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return _serialize_trade(trade)


@router.put("/{trade_id}")
def update_trade(
    trade_id: UUID,
    payload: TradeUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    trade = db.get(Trade, trade_id)
    if trade is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Trade not found")

    updates = payload.model_dump(exclude_unset=True)
    if (
        updates.get("observation_id") is not None
        and db.get(ListingObservation, updates["observation_id"]) is None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Listing observation not found")
    new_status = updates.get("status", trade.status)
    try:
        validate_status_transition(trade.status, new_status)
        merged = {**_trade_values(trade), **updates}
        validated = TradeCreate.model_validate(merged)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    for field, value in validated.model_dump().items():
        setattr(trade, field, value)
    db.commit()
    db.refresh(trade)
    return _serialize_trade(trade)


@router.get("")
def list_trades(
    trade_status: TradeStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = db.query(Trade)
    if trade_status is not None:
        query = query.filter(Trade.status == trade_status)
    total = query.count()
    trades = (
        query.order_by(desc(Trade.acquired_on), desc(Trade.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "trades": [_serialize_trade(trade) for trade in trades],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/summary/monthly")
def monthly_summary(
    month: date | None = Query(None, description="First day of month, for example 2026-09-01"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    selected_month = _first_of_month(month)
    trades = db.query(Trade).all()
    overheads = db.query(MonthlyOverhead).filter(MonthlyOverhead.month == selected_month).all()
    return summarize_month(trades, overheads, month=selected_month)


@router.put("/overheads/monthly")
def upsert_monthly_overhead(
    payload: MonthlyOverheadInput,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    overhead = db.get(MonthlyOverhead, payload.month)
    created = overhead is None
    if overhead is None:
        overhead = MonthlyOverhead(**payload.model_dump())
        db.add(overhead)
    else:
        overhead.amount_eur = payload.amount_eur
        overhead.notes = payload.notes
    db.commit()
    db.refresh(overhead)
    return {
        "month": overhead.month,
        "amount_eur": overhead.amount_eur,
        "notes": overhead.notes,
        "created": created,
    }


@router.get("/overheads/monthly")
def list_monthly_overheads(db: Session = Depends(get_db)) -> dict[str, Any]:
    overheads = db.query(MonthlyOverhead).order_by(desc(MonthlyOverhead.month)).all()
    return {
        "overheads": [
            {"month": row.month, "amount_eur": row.amount_eur, "notes": row.notes}
            for row in overheads
        ]
    }


@router.get("/export.csv", response_class=Response)
def export_trades_csv(db: Session = Depends(get_db)) -> Response:
    trades = db.query(Trade).order_by(desc(Trade.acquired_on), desc(Trade.created_at)).all()
    rows = [_serialize_trade(trade) for trade in trades]
    columns = [
        "trade_id",
        *TRADE_INPUT_FIELDS,
        "actual_costs_eur",
        "recognized_revenue_eur",
        "realized_profit_eur",
        "inventory_capital_eur",
        "forecast_profit_eur",
        "age_days",
        "close_date",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trade-ledger.csv"},
    )

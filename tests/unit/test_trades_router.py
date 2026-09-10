from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.routers.trades import MonthlyOverheadInput, TradeCreate, TradeUpdate, router
from libs.common.db import get_db
from libs.common.models import ListingObservation
from libs.common.trade_models import MonthlyOverhead, Trade


def _valid_trade() -> dict[str, object]:
    return {
        "title": "Sony A7 III body",
        "buy_platform": "leboncoin",
        "exit_platform": "ebay",
        "status": "purchased",
        "acquired_on": date(2026, 8, 1),
        "acquisition_price_eur": Decimal("650.00"),
    }


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_trade_input_rejects_nonfinite_money(value: Decimal) -> None:
    payload = _valid_trade()
    payload["acquisition_price_eur"] = value

    with pytest.raises(ValidationError):
        TradeCreate.model_validate(payload)


def test_trade_input_rejects_negative_actual_cost() -> None:
    payload = _valid_trade()
    payload["repair_cost_eur"] = Decimal("-0.01")

    with pytest.raises(ValidationError):
        TradeCreate.model_validate(payload)


def test_trade_input_rejects_revenue_before_sale() -> None:
    payload = _valid_trade()
    payload["sale_revenue_eur"] = Decimal("700.00")

    with pytest.raises(ValidationError, match="sale_revenue_eur is only valid"):
        TradeCreate.model_validate(payload)


def test_trade_input_rejects_future_actual_dates() -> None:
    payload = _valid_trade()
    payload["acquired_on"] = date.today() + timedelta(days=1)

    with pytest.raises(ValidationError, match="actual trade dates cannot be in the future"):
        TradeCreate.model_validate(payload)


def test_zero_proceeds_written_off_trade_is_valid() -> None:
    payload = _valid_trade()
    payload.update(
        {
            "status": "written_off",
            "closed_on": date(2026, 8, 20),
            "sale_revenue_eur": Decimal("0.00"),
        }
    )

    trade = TradeCreate.model_validate(payload)

    assert trade.sale_revenue_eur == Decimal("0.00")


def test_partial_update_does_not_supply_defaults_for_omitted_costs() -> None:
    update = TradeUpdate.model_validate({"status": "listed"})

    assert update.model_dump(exclude_unset=True) == {"status": "listed"}


def test_monthly_overhead_uses_first_day_as_unique_month_key() -> None:
    with pytest.raises(ValidationError, match="first day"):
        MonthlyOverheadInput(month=date(2026, 8, 15), amount_eur=Decimal("150.00"))


class _FakeQuery:
    def filter(self, *_args: object) -> "_FakeQuery":
        return self

    def all(self) -> list[object]:
        return []


class _FakeDb:
    def __init__(self) -> None:
        self.overhead: MonthlyOverhead | None = None

    def get(self, model: object, key: object) -> object | None:
        if model is ListingObservation:
            return None
        if model is MonthlyOverhead:
            return self.overhead
        return None

    def add(self, value: object) -> None:
        if isinstance(value, MonthlyOverhead):
            self.overhead = value
        if isinstance(value, Trade):
            value.trade_id = uuid4()
            value.created_at = datetime.now(UTC)
            value.updated_at = datetime.now(UTC)

    def commit(self) -> None:
        return None

    def refresh(self, _value: object) -> None:
        return None

    def query(self, _model: object) -> _FakeQuery:
        return _FakeQuery()


@pytest.fixture()
def trades_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    fake_db = _FakeDb()
    app.dependency_overrides[get_db] = lambda: fake_db
    return TestClient(app, raise_server_exceptions=False)


def test_summary_defaults_to_current_calendar_month(trades_client: TestClient) -> None:
    response = trades_client.get("/trades/summary/monthly")

    assert response.status_code == 200
    assert response.json()["month"] == date.today().replace(day=1).isoformat()


def test_overhead_static_route_is_not_captured_as_trade_uuid(trades_client: TestClient) -> None:
    response = trades_client.put(
        "/trades/overheads/monthly",
        json={"month": "2026-08-01", "amount_eur": "150.00"},
    )

    assert response.status_code == 200
    assert response.json()["amount_eur"] == "150.00"


def test_create_rejects_unknown_observation_link(trades_client: TestClient) -> None:
    payload = _valid_trade()
    payload["observation_id"] = 999
    payload = {
        key: value.isoformat()
        if isinstance(value, date)
        else str(value)
        if isinstance(value, Decimal)
        else value
        for key, value in payload.items()
    }

    response = trades_client.post("/trades", json=payload)

    assert response.status_code == 404
    assert response.json()["detail"] == "Listing observation not found"

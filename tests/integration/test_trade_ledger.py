from collections.abc import Generator
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.routers.trades import router
from libs.common.db import get_db
from libs.common.trades import query_capital_committed


def test_trade_api_lifecycle_and_monthly_profit(integration_db: Session) -> None:
    app = FastAPI()
    app.include_router(router)

    def override_db() -> Generator[Session, None, None]:
        yield integration_db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    purchase = client.post(
        "/trades",
        json={
            "title": "Sony A7 III body",
            "buy_platform": "leboncoin",
            "exit_platform": "ebay",
            "acquired_on": "2026-07-01",
            "acquisition_price_eur": "300.00",
            "acquisition_fees_eur": "5.00",
            "inbound_logistics_eur": "10.00",
            "forecast_sale_revenue_eur": "450.00",
            "forecast_remaining_costs_eur": "55.00",
        },
    )
    assert purchase.status_code == 201
    trade_id = purchase.json()["trade_id"]
    assert purchase.json()["recognized_revenue_eur"] == "0.00"
    assert purchase.json()["inventory_capital_eur"] == "315.00"

    listed = client.put(f"/trades/{trade_id}", json={"status": "listed"})
    assert listed.status_code == 200
    assert listed.json()["status"] == "listed"

    sold = client.put(
        f"/trades/{trade_id}",
        json={
            "status": "sold",
            "sold_on": "2026-07-20",
            "sale_revenue_eur": "500.00",
            "selling_fees_eur": "50.00",
            "outbound_logistics_eur": "12.00",
            "refund_cost_eur": "8.00",
            "turnover_charges_eur": "61.50",
        },
    )
    assert sold.status_code == 200
    assert sold.json()["recognized_revenue_eur"] == "0.00"
    assert sold.json()["realized_profit_eur"] is None

    settled = client.put(
        f"/trades/{trade_id}",
        json={"status": "settled", "settled_on": "2026-08-02"},
    )
    assert settled.status_code == 200
    assert settled.json()["actual_costs_eur"] == "446.50"
    assert settled.json()["realized_profit_eur"] == "53.50"
    assert settled.json()["forecast_profit_eur"] is None
    assert str(query_capital_committed(integration_db, as_of=date(2026, 8, 1))) == "446.50"

    returned = client.post(
        "/trades",
        json={
            "title": "Supplier return",
            "buy_platform": "leboncoin",
            "exit_platform": "supplier_return",
            "status": "returned",
            "acquired_on": "2026-08-01",
            "closed_on": "2026-08-15",
            "acquisition_price_eur": "100.00",
            "sale_revenue_eur": "20.00",
        },
    )
    assert returned.status_code == 201
    assert returned.json()["realized_profit_eur"] == "-80.00"

    written_off = client.post(
        "/trades",
        json={
            "title": "Damaged beyond repair",
            "buy_platform": "vinted",
            "exit_platform": "write_off",
            "status": "written_off",
            "acquired_on": "2026-08-05",
            "closed_on": "2026-08-20",
            "acquisition_price_eur": "50.00",
            "sale_revenue_eur": "0.00",
        },
    )
    assert written_off.status_code == 201
    assert written_off.json()["realized_profit_eur"] == "-50.00"

    overhead = client.put(
        "/trades/overheads/monthly",
        json={"month": "2026-08-01", "amount_eur": "150.00"},
    )
    assert overhead.status_code == 200

    summary = client.get("/trades/summary/monthly", params={"month": "2026-08-01"})
    assert summary.status_code == 200
    assert summary.json() == {
        "month": "2026-08-01",
        "realized_revenue_eur": "520.00",
        "realized_trade_profit_eur": "-76.50",
        "monthly_overhead_eur": "150.00",
        "operating_profit_eur": "-226.50",
        "closed_trade_count": 3,
        "inventory_count": 0,
        "inventory_capital_eur": "0.00",
        "aged_inventory_count": 0,
        "inventory_as_of": date.today().isoformat(),
    }

    listing = client.get("/trades")
    assert listing.status_code == 200
    assert listing.json()["total"] == 3

    exported = client.get("/trades/export.csv")
    assert exported.status_code == 200
    assert exported.text.count("\n") == 4

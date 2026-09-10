from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from ui.lib import api


class _Response:
    def __init__(self, payload: dict[str, Any], content: bytes = b"") -> None:
        self._payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_inventory_page_renders_purchase_overhead_and_export_workflows(
    monkeypatch: MonkeyPatch,
) -> None:
    requested_paths: list[str] = []

    def fake_get(path: str, **_kwargs: Any) -> _Response:
        requested_paths.append(path)
        if path == "/trades":
            return _Response(
                {
                    "trades": [
                        {
                            "trade_id": "6cf3f5ac-5726-4c58-b0c0-bd52116f434f",
                            "title": "Sony A7 III body",
                            "status": "purchased",
                            "buy_platform": "leboncoin",
                            "exit_platform": "ebay",
                            "acquired_on": "2026-08-01",
                            "sold_on": None,
                            "settled_on": None,
                            "closed_on": None,
                            "acquisition_price_eur": "300.00",
                            "acquisition_fees_eur": "5.00",
                            "inbound_logistics_eur": "10.00",
                            "repair_cost_eur": "0.00",
                            "selling_fees_eur": "0.00",
                            "outbound_logistics_eur": "0.00",
                            "refund_cost_eur": "0.00",
                            "turnover_charges_eur": "0.00",
                            "sale_revenue_eur": None,
                            "forecast_sale_revenue_eur": "450.00",
                            "forecast_remaining_costs_eur": "55.00",
                            "time_spent_minutes": 20,
                            "notes": None,
                            "age_days": 30,
                            "inventory_capital_eur": "315.00",
                            "forecast_profit_eur": "80.00",
                            "recognized_revenue_eur": "0.00",
                            "actual_costs_eur": "315.00",
                            "realized_profit_eur": None,
                        }
                    ],
                    "total": 1,
                }
            )
        if path == "/trades/summary/monthly":
            return _Response(
                {
                    "operating_profit_eur": "-150.00",
                    "realized_trade_profit_eur": "0.00",
                    "realized_revenue_eur": "0.00",
                    "inventory_capital_eur": "0.00",
                    "aged_inventory_count": 0,
                    "closed_trade_count": 0,
                    "inventory_count": 0,
                    "monthly_overhead_eur": "150.00",
                }
            )
        if path == "/trades/export.csv":
            return _Response({}, content=b"trade_id,title\n1,Sony A7 III body\n")
        raise AssertionError(f"Unexpected API path: {path}")

    monkeypatch.setattr(api, "api_get", fake_get)
    page = Path(__file__).parents[2] / "ui" / "pages" / "8_Inventory.py"

    app = AppTest.from_file(page)
    app.run()

    assert not app.exception
    assert app.markdown[0].value == "# Inventory & Realized P&L"
    assert [tab.label for tab in app.tabs] == [
        "Record purchase",
        "Update / settle",
        "Monthly overhead",
    ]
    overhead = next(widget for widget in app.number_input if widget.label == "Monthly overhead (€)")
    assert overhead.value == 150.0
    assert "/trades/export.csv" in requested_paths


def test_writing_off_sold_trade_preserves_recorded_sale_date(monkeypatch: MonkeyPatch) -> None:
    trade = {
        "trade_id": "6cf3f5ac-5726-4c58-b0c0-bd52116f434f",
        "title": "Returned camera",
        "status": "sold",
        "buy_platform": "leboncoin",
        "exit_platform": "ebay",
        "acquired_on": "2026-08-01",
        "sold_on": "2026-08-02",
        "settled_on": None,
        "closed_on": None,
        "acquisition_price_eur": "100",
        "sale_revenue_eur": "0",
        "recognized_revenue_eur": "0",
        "actual_costs_eur": "100",
        "realized_profit_eur": None,
        "age_days": 30,
        "inventory_capital_eur": "100",
        "forecast_profit_eur": None,
    }
    saved: list[dict[str, Any]] = []

    def fake_get(path: str, **_kwargs: Any) -> _Response:
        if path == "/trades":
            return _Response({"trades": [trade], "total": 1})
        if path == "/trades/export.csv":
            return _Response({}, content=b"trade_id,title\n")
        return _Response({})

    def fake_put(path: str, **kwargs: Any) -> _Response:
        saved.append(kwargs["json"])
        response = _Response({})
        response.status_code = 200
        return response

    monkeypatch.setattr(api, "api_get", fake_get)
    monkeypatch.setattr(api, "api_put", fake_put)
    app = AppTest.from_file(Path(__file__).parents[2] / "ui/pages/8_Inventory.py").run()
    next(widget for widget in app.selectbox if widget.label == "New status").select(
        "written_off"
    ).run()
    next(widget for widget in app.button if widget.label == "Save trade").click().run()
    assert not app.exception
    assert saved[0]["sold_on"] == "2026-08-02"

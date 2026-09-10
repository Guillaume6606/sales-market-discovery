from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from ui.lib import api


class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def test_valuation_page_evaluates_decimal_strings_without_claiming_unknown_is_zero(
    monkeypatch: MonkeyPatch,
) -> None:
    requested_paths: list[str] = []

    def fake_products(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{"product_id": "product-1", "name": "Sony WH-1000XM5"}]

    def fake_get(path: str, **_kwargs: Any) -> _Response:
        requested_paths.append(path)
        if path == "/valuation/references":
            return _Response([])
        if path == "/valuation/listings/41":
            return _Response(
                {
                    "eligible": False,
                    "reasons": ["listing_missing_shipping_cost"],
                    "estimated_sale_price_eur": "500.00",
                    "acquisition_cost_eur": None,
                    "contribution_eur": "-12.35",
                    "max_buy_price_eur": "309.31",
                    "cost_breakdown": None,
                    "reference_snapshot": {"reviewed_by": "operator@example.com"},
                }
            )
        raise AssertionError(f"Unexpected API path: {path}")

    monkeypatch.setattr(api, "fetch_products", fake_products)
    monkeypatch.setattr(api, "api_get", fake_get)
    page = Path(__file__).parents[2] / "ui" / "pages" / "7_Valuation.py"

    app = AppTest.from_file(page)
    app.run()
    observation_id = next(widget for widget in app.number_input if widget.label == "Observation ID")
    observation_id.set_value(41)
    next(button for button in app.button if button.label == "Evaluate listing").click()
    app.run()

    assert not app.exception
    assert "/valuation/listings/41" in requested_paths
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics == {
        "Exit estimate": "€500.00",
        "Acquisition cost": "Unknown",
        "Contribution": "€-12.35",
        "Maximum buy": "€309.31",
    }

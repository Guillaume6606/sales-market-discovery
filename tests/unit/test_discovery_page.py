from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from ui.lib import api

PAGE = Path(__file__).parents[2] / "ui" / "pages" / "1_Discovery.py"


def _install_fakes(monkeypatch: MonkeyPatch, calls: list[dict[str, Any]]) -> None:
    def fake_discovery(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "items": [
                {
                    "product_id": "product-1",
                    "title": "Sony WH-1000XM4",
                    "brand": None,
                    "pmn": 187.73,
                    "pmn_confidence": 0.2,
                    "price_min_market": 140.0,
                    "delta_vs_pmn_pct": -25.42,
                    "liquidity_score": 31.67,
                    "trend_score": None,
                }
            ],
            "total": 1,
            "offset": 0,
            "limit": 50,
        }

    monkeypatch.setattr(api, "fetch_discovery", fake_discovery)
    monkeypatch.setattr(api, "fetch_analytics", lambda: None)
    monkeypatch.setattr(api, "fetch_category_names", lambda: [])


def test_discovery_renders_products_without_trend_score(monkeypatch: MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_fakes(monkeypatch, calls)

    app = AppTest.from_file(PAGE)
    app.run()

    assert not app.exception
    assert app.dataframe


def test_liquidity_filter_uses_the_backend_zero_to_hundred_scale(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _install_fakes(monkeypatch, calls)

    app = AppTest.from_file(PAGE)
    app.run()
    app.radio(key="preset_select").set_value("Fast Movers")
    app.run()

    liquidity = next(slider for slider in app.slider if slider.label == "Min Liquidity")
    assert liquidity.max == 100.0
    assert calls[-1]["min_liquidity"] == 80.0


def test_high_margin_preset_applies_its_discount(monkeypatch: MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    _install_fakes(monkeypatch, calls)

    app = AppTest.from_file(PAGE)
    app.run()
    app.radio(key="preset_select").set_value("High Margin")
    app.run()

    assert calls[-1]["min_margin"] == -20

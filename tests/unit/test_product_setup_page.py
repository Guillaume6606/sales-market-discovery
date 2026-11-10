from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from ui.lib import api

PAGE = Path(__file__).parents[2] / "ui" / "pages" / "3_Product_Setup.py"

CATEGORY = {"category_id": "cat-1", "name": "Audio", "description": None}
PRODUCTS: list[dict[str, Any]] = [
    {
        "product_id": "product-switch",
        "name": "Nintendo Switch OLED",
        "description": None,
        "search_query": "switch oled",
        "brand": "Nintendo",
        "price_min": 150.0,
        "price_max": None,
        "providers": ["vinted"],
        "words_to_avoid": ["boite"],
        "ingestion_interval_minutes": 120,
        "enable_llm_validation": False,
        "is_active": False,
        "category": CATEGORY,
        "last_ingested_at": None,
    },
    {
        "product_id": "product-sony",
        "name": "Sony WH-1000XM4",
        "description": None,
        "search_query": "sony wh-1000xm4",
        "brand": None,
        "price_min": None,
        "price_max": None,
        "providers": ["ebay", "leboncoin"],
        "words_to_avoid": [],
        "ingestion_interval_minutes": 15,
        "enable_llm_validation": False,
        "is_active": True,
        "category": CATEGORY,
        "last_ingested_at": None,
    },
]


class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> Any:
        return self._payload


def _install_fakes(monkeypatch: MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    writes: list[tuple[str, dict[str, Any]]] = []

    def fake_put(path: str, **kwargs: Any) -> _Response:
        writes.append((path, kwargs["json"]))
        return _Response({}, 200)

    def fake_post(path: str, **kwargs: Any) -> _Response:
        writes.append((path, kwargs["json"]))
        return _Response({}, 201)

    monkeypatch.setattr(api, "fetch_products", lambda *_a, **_k: PRODUCTS)
    monkeypatch.setattr(api, "fetch_categories", lambda *_a, **_k: [CATEGORY])
    monkeypatch.setattr(api, "fetch_category_names", lambda: ["Audio"])
    monkeypatch.setattr(api, "fetch_ingestion_runs", lambda *_a, **_k: {"runs": [], "total": 0})
    monkeypatch.setattr(api, "api_get", lambda *_a, **_k: _Response({"categories": [CATEGORY]}))
    monkeypatch.setattr(api, "api_put", fake_put)
    monkeypatch.setattr(api, "api_post", fake_post)
    return writes


def _form_values(app: AppTest) -> dict[str, Any]:
    return {
        "name": next(w for w in app.text_input if w.label == "Name *").value,
        "query": next(w for w in app.text_input if w.label == "Search Query *").value,
        "brand": next(w for w in app.text_input if w.label == "Brand (optional)").value,
        "providers": next(w for w in app.multiselect if w.label == "Providers").value,
        "active": next(w for w in app.checkbox if w.label == "Active").value,
        "interval": next(w for w in app.number_input if w.label.startswith("Scan interval")).value,
    }


def test_edit_form_loads_each_selected_product_after_create_mode(
    monkeypatch: MonkeyPatch,
) -> None:
    _install_fakes(monkeypatch)
    app = AppTest.from_file(PAGE)
    app.run()
    app.radio(key="product_mode").set_value("Edit Existing Product")
    app.run()

    for product in PRODUCTS:
        app.selectbox(key="edit_product_select").set_value(
            f"{product['name']} ({product['brand'] or 'No brand'})"
        )
        app.run()

        assert not app.exception
        assert _form_values(app) == {
            "name": product["name"],
            "query": product["search_query"],
            "brand": product["brand"],
            "providers": product["providers"],
            "active": product["is_active"],
            "interval": product["ingestion_interval_minutes"],
        }


def test_update_preserves_unchanged_settings_of_selected_product(
    monkeypatch: MonkeyPatch,
) -> None:
    writes = _install_fakes(monkeypatch)
    app = AppTest.from_file(PAGE)
    app.run()
    app.radio(key="product_mode").set_value("Edit Existing Product")
    app.run()
    app.selectbox(key="edit_product_select").set_value("Nintendo Switch OLED (Nintendo)")
    app.run()

    next(button for button in app.button if button.label == "Update Product").click()
    app.run()

    assert writes == [
        (
            "/products/product-switch",
            {
                "name": "Nintendo Switch OLED",
                "description": None,
                "search_query": "switch oled",
                "category_id": "cat-1",
                "brand": "Nintendo",
                "price_min": 150.0,
                "price_max": None,
                "providers": ["vinted"],
                "words_to_avoid": ["boite"],
                "enable_llm_validation": False,
                "ingestion_interval_minutes": 120,
                "is_active": False,
            },
        )
    ]


def test_create_refuses_an_empty_provider_selection(monkeypatch: MonkeyPatch) -> None:
    writes = _install_fakes(monkeypatch)
    app = AppTest.from_file(PAGE)
    app.run()
    next(w for w in app.text_input if w.label == "Name *").input("Pixel 8")
    next(w for w in app.text_input if w.label == "Search Query *").input("pixel 8")
    next(w for w in app.multiselect if w.label == "Providers").set_value([])
    next(button for button in app.button if button.label == "Create Product").click()
    app.run()

    assert writes == []
    assert any("provider" in error.value.lower() for error in app.error)

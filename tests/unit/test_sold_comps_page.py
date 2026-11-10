from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from ui.lib import api

PROPOSAL = {
    "proposal_id": "11111111-1111-1111-1111-111111111111",
    "product_id": "product-1",
    "condition": "good",
    "status": "insufficient",
    "search_query": "Sony WH-1000XM4",
    "search_url": "https://www.ebay.fr/sch/i.html?_nkw=Sony",
    "required_tokens": ["Sony", "WH-1000XM4"],
    "excluded_tokens": [],
    "sale_count": 3,
    "min_comparables": 8,
    "median_price_eur": "142.50",
    "p25_price_eur": "137.75",
    "p75_price_eur": "147.25",
    "first_sold_at": "2026-09-05T00:00:00Z",
    "last_sold_at": "2026-11-03T00:00:00Z",
    "computed_at": "2026-11-10T00:00:00Z",
    "sales": [{"item_id": "356001000001", "price_eur": "145.00"}],
    "excluded_sales": [{"item_id": "356001000003", "reason": "best_offer_price_not_final"}],
}


class _Response:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def test_insufficient_proposal_cannot_be_approved(monkeypatch: MonkeyPatch) -> None:
    def fake_get(path: str, **_kwargs: Any) -> _Response:
        if path == "/sold-comps/proposals":
            return _Response([PROPOSAL])
        if path == f"/sold-comps/proposals/{PROPOSAL['proposal_id']}":
            return _Response(PROPOSAL)
        raise AssertionError(f"Unexpected API path: {path}")

    monkeypatch.setattr(
        api, "fetch_products", lambda *_a, **_k: [{"product_id": "product-1", "name": "Sony"}]
    )
    monkeypatch.setattr(api, "api_get", fake_get)
    page = Path(__file__).parents[2] / "ui" / "pages" / "9_Sold_Comps.py"

    app = AppTest.from_file(page)
    app.run()

    assert not app.exception
    labels = [button.label for button in app.button]
    assert "Approve and create reference" not in labels
    assert "Reject proposal" in labels
    assert any("Insufficient evidence" in warning.value for warning in app.warning)

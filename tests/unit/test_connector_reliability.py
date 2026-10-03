"""Regression tests for connector reliability fixes (eBay rating, LBC paging, Vinted condition)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from bs4 import BeautifulSoup

from ingestion.connectors.ebay import parse_ebay_browse_response
from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector
from ingestion.connectors.vinted import VintedConnector
from ingestion.connectors.vinted_api import VintedAPIConnector


def _browse_item(seller: dict) -> dict:
    return {
        "itemId": "v1|123456|0",
        "legacyItemId": "123456",
        "title": "Sony WH-1000XM4",
        "price": {"value": "199.99", "currency": "EUR"},
        "condition": "Used",
        "itemWebUrl": "https://www.ebay.fr/itm/123456",
        "seller": seller,
    }


class TestEbaySellerRatingSemantics:
    def test_feedback_percentage_maps_to_five_point_rating(self):
        item = _browse_item({"username": "s", "feedbackScore": 1500, "feedbackPercentage": "99.0"})
        listings = parse_ebay_browse_response({"total": 1, "itemSummaries": [item]}, is_sold=False)

        assert listings[0].seller_rating == 4.95

    def test_feedback_score_alone_is_not_a_rating(self):
        item = _browse_item({"username": "s", "feedbackScore": 1500})
        listings = parse_ebay_browse_response({"total": 1, "itemSummaries": [item]}, is_sold=False)

        assert listings[0].seller_rating is None


class _FakeLbcResponse:
    def __init__(self, ads):
        self.ads = ads


class _FakeLbcClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def search(self, **filters):
        self.calls.append(filters)
        page = filters["page"]
        if page > 11:
            raise RuntimeError("pagination exceeded bounded test sentinel")
        if callable(self.pages):
            return _FakeLbcResponse(self.pages(page))
        return _FakeLbcResponse(self.pages.get(page, []))


class TestLeBonCoinAPIPagination:
    async def test_page_without_mappable_ads_stops_pagination(self):
        unmappable = [{"subject": "no id here", "price": 10} for _ in range(35)]
        connector = LeBonCoinAPIConnector(client=_FakeLbcClient(lambda page: unmappable))

        results = await connector.search_items(keyword="airpods", limit=100)

        assert results == []
        assert len(connector._client.calls) == 1

    async def test_pagination_is_capped(self):
        def full_page(page):
            base = page * 1000
            return [{"list_id": base + i, "subject": "AirPods", "price": 50} for i in range(35)]

        connector = LeBonCoinAPIConnector(client=_FakeLbcClient(full_page))

        results = await connector.search_items(keyword="airpods", limit=10_000)

        assert len(connector._client.calls) == LeBonCoinAPIConnector.MAX_PAGES
        assert len(results) == 35 * LeBonCoinAPIConnector.MAX_PAGES

    async def test_pagination_continues_to_next_page(self):
        pages = {
            1: [{"list_id": i, "subject": "AirPods", "price": 50} for i in range(1, 36)],
            2: [{"list_id": 100 + i, "subject": "AirPods", "price": 50} for i in range(5)],
        }
        connector = LeBonCoinAPIConnector(client=_FakeLbcClient(pages))

        results = await connector.search_items(keyword="airpods", limit=40)

        assert len(results) == 40
        assert [call["page"] for call in connector._client.calls] == [1, 2]


class TestVintedHtmlCondition:
    def test_condition_is_extracted_when_price_is_present(self):
        html = (
            '<div class="item"><a href="/items/123456-airpods-pro">AirPods Pro</a>'
            '<span class="price">45,00 €</span><span>Très bon état</span></div>'
        )
        element = BeautifulSoup(html, "html.parser").select_one("div.item")

        parsed = VintedConnector()._parse_item_element(element)

        assert parsed is not None
        assert parsed["price"] == 45.0
        assert parsed["condition"] == "très bon état"


@pytest.mark.parametrize("percentage", [None, "invalid", "NaN", "Infinity", -1, 101, True])
def test_ebay_invalid_feedback_is_unknown(percentage: Any) -> None:
    item = _browse_item({"feedbackScore": 1500, "feedbackPercentage": percentage})
    listing = parse_ebay_browse_response({"itemSummaries": [item]})[0]
    assert listing.seller_rating is None


@pytest.mark.parametrize("percentage, expected", [(0, 0.0), (100, 5.0), ("98.4", 4.92)])
def test_ebay_feedback_rating_boundaries(percentage: Any, expected: float) -> None:
    item = _browse_item({"feedbackPercentage": percentage})
    listing = parse_ebay_browse_response({"itemSummaries": [item]})[0]
    assert listing.seller_rating == expected


@pytest.mark.parametrize(
    "shipping_html, expected",
    [
        ("", None),
        ('<span class="shipping">Livraison disponible</span>', None),
        ('<span class="shipping">Livraison gratuite</span>', 0.0),
        ('<span class="shipping">Livraison 0,00 €</span>', 0.0),
        ('<span class="shipping">Livraison 3,50 €</span>', 3.5),
    ],
)
def test_vinted_shipping_requires_price_or_explicit_free_evidence(
    shipping_html: str, expected: float | None
) -> None:
    html = (
        '<div class="item"><a href="/items/123456-airpods-pro">AirPods Pro</a>'
        '<span class="price">45,00 €</span>' + shipping_html + "</div>"
    )
    element = BeautifulSoup(html, "html.parser").select_one("div.item")
    parsed = VintedConnector()._parse_item_element(element)
    assert parsed is not None
    assert parsed["shipping_cost"] == expected


async def test_leboncoin_listing_count_is_not_transaction_count() -> None:
    ad = SimpleNamespace(user=SimpleNamespace(registered_at=None, total_ads=123))
    client = SimpleNamespace(get_ad=lambda _: ad)
    detail = await LeBonCoinAPIConnector(client=client).fetch_detail("123", 1)
    assert detail is not None
    assert detail.seller_transaction_count is None


async def test_vinted_feedback_and_item_counts_are_not_transaction_count(monkeypatch) -> None:
    item = SimpleNamespace(
        description="Good condition",
        photos=[],
        json_data={},
        user=SimpleNamespace(item_count=30, feedback_count=20),
        favourite_count=2,
        view_count=10,
    )

    class Scraper:
        async def item(self, listing_id: str) -> Any:
            return item

    @asynccontextmanager
    async def session(self):
        yield Scraper()

    monkeypatch.setattr(VintedAPIConnector, "_session", session)
    detail = await VintedAPIConnector().fetch_detail("123", 1)
    assert detail is not None
    assert detail.seller_transaction_count is None


@pytest.mark.parametrize(
    "percentage, expected",
    [("98.5", 4.92), ("99.5", 4.98), ("99.7", 4.98), ("99.9", 5.0)],
)
def test_ebay_rating_decimal_half_even_rounding(percentage: str, expected: float) -> None:
    item = _browse_item({"feedbackPercentage": percentage})
    listing = parse_ebay_browse_response({"itemSummaries": [item]})[0]
    assert listing.seller_rating == expected


def test_ebay_detail_feedback_count_is_not_transaction_count(monkeypatch) -> None:
    import httpx

    from ingestion.connectors import ebay

    monkeypatch.setattr(ebay, "_credentials_ready", lambda: True)
    monkeypatch.setattr(ebay, "_get_app_token_sync", lambda: "test-token")
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.ebay.com/test"),
        json={"itemId": "v1|123456|0", "seller": {"feedbackScore": 1500}},
    )
    monkeypatch.setattr(ebay.httpx, "get", lambda *args, **kwargs: response)
    detail = ebay.fetch_detail("v1|123456|0", 1)
    assert detail is not None
    assert detail.seller_transaction_count is None


async def test_leboncoin_denial_is_not_retried_by_sdk(monkeypatch) -> None:
    from ingestion.connectors import leboncoin_api

    calls = []

    class Session:
        def request(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(ok=False, status_code=403)

    monkeypatch.setattr(leboncoin_api.lbc.Client, "_init_session", lambda *a, **k: Session())
    monkeypatch.setattr(leboncoin_api.settings, "scraping_proxy_url", None)
    connector = leboncoin_api.LeBonCoinAPIConnector()
    with pytest.raises(RuntimeError):
        await connector.search_items(keyword="test")
    assert len(calls) == 1


async def test_leboncoin_bootstrap_runs_off_event_loop(monkeypatch) -> None:
    import threading

    from ingestion.connectors import leboncoin_api

    caller_thread = threading.get_ident()
    bootstrap_threads = []

    class Session:
        def request(self, **kwargs):
            return SimpleNamespace(ok=True, json=lambda: {"ads": []})

    def bootstrap(*args, **kwargs):
        bootstrap_threads.append(threading.get_ident())
        return Session()

    monkeypatch.setattr(leboncoin_api.lbc.Client, "_init_session", bootstrap)
    assert await leboncoin_api.fetch_leboncoin_api_listings("test") == []
    assert len(bootstrap_threads) == 1
    assert bootstrap_threads[0] != caller_thread

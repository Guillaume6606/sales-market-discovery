"""Regression tests for connector reliability fixes (eBay rating, LBC paging, Vinted condition)."""

from bs4 import BeautifulSoup

from ingestion.connectors.ebay import parse_ebay_browse_response
from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector
from ingestion.connectors.vinted import VintedConnector


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
        assert parsed["condition"] == "très bon"

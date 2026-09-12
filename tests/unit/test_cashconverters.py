import json

import httpx
import pytest

from ingestion.connectors.cashconverters import (
    CashConvertersConnector,
    parse_product_offers,
)

PRODUCT_HTML = """
<html>
  <head><link rel="canonical" href="https://www.cashconverters.fr/product/google-pixel8-5g-double-sim-128go-noir-25536736"></head>
  <body>
    <div class="product-block"><h1>Google Pixel8 5G Double SIM 128Go - Noir</h1></div>
    <div class="product-offer state-selector">
      <div class="options">
        <a href="/product-offer/306390" class="option selected" data-state="a"
           data-state-label="Très bon état" data-price="319.99">
          <span class="product-state">Très bon état</span>
          <span class="product-price">319,99&nbsp;€</span>
          <span class="store-name">LA CHAPELLE SAINT AUBIN</span>
        </a>
        <a href="/product-offer/306753" class="option" data-state="a"
           data-state-label="Très bon état" data-price="319.99">
          <span class="product-state">Très bon état</span>
          <span class="product-price">319,99&nbsp;€</span>
          <span class="store-name">LA CHAPELLE SAINT AUBIN</span>
        </a>
      </div>
    </div>
    <div class="foldable-content delivery">
      <h3>La livraison à domicile</h3>
      <p>Livraison à domicile : livraison sous 2 à 5 jours ouvrés.</p>
      <h3>Le Retrait en magasin (Click &amp; Collect)</h3>
    </div>
  </body>
</html>
"""


def test_parse_product_offers_splits_store_offers() -> None:
    listings = parse_product_offers(
        PRODUCT_HTML,
        "https://www.cashconverters.fr/product/google-pixel8-5g-double-sim-128go-noir-25536736",
    )

    assert [listing.listing_id for listing in listings] == ["306390", "306753"]
    assert all(listing.title == "Google Pixel8 5G Double SIM 128Go - Noir" for listing in listings)
    assert all(listing.price == 319.99 for listing in listings)
    assert all(listing.currency == "EUR" for listing in listings)
    assert all(listing.condition_raw == "Très bon état" for listing in listings)
    assert all(listing.condition_norm == "like_new" for listing in listings)
    assert all(listing.location == "LA CHAPELLE SAINT AUBIN" for listing in listings)
    assert all(listing.shipping_cost is None for listing in listings)
    assert listings[0].delivery_to_france is True
    assert listings[0].delivery_evidence is not None
    assert "France métropolitaine" in listings[0].delivery_evidence
    assert listings[1].delivery_to_france is None
    assert listings[1].delivery_evidence is None
    assert listings[0].url == "https://www.cashconverters.fr/product-offer/306390"


def test_parse_product_offer_fails_closed_without_delivery_evidence() -> None:
    html = PRODUCT_HTML.replace(
        '<div class="foldable-content delivery">', '<div class="foldable-content returns">'
    )

    listing = parse_product_offers(html, "https://www.cashconverters.fr/product/example")[0]

    assert listing.delivery_to_france is None
    assert listing.delivery_evidence is None
    assert listing.shipping_cost is None


def test_parse_product_offer_marks_explicit_pickup_only() -> None:
    html = PRODUCT_HTML.replace(
        "<h3>La livraison à domicile</h3>\n      <p>Livraison à domicile : livraison sous 2 à 5 jours ouvrés.</p>",
        "<h3>Retrait en magasin uniquement</h3>",
    )

    listing = parse_product_offers(html, "https://www.cashconverters.fr/product/example")[0]

    assert listing.delivery_to_france is False
    assert listing.delivery_evidence == "Offre indiquée en retrait en magasin uniquement"


def test_parse_product_offer_accepts_only_explicit_free_shipping() -> None:
    html = PRODUCT_HTML.replace(
        '<span class="store-name">LA CHAPELLE SAINT AUBIN</span>',
        '<span class="store-name">LA CHAPELLE SAINT AUBIN</span>'
        '<span class="delivery-badge">Livraison Offerte</span>',
        1,
    )

    listings = parse_product_offers(html, "https://www.cashconverters.fr/product/example")

    assert listings[0].shipping_cost == 0.0
    assert listings[0].delivery_to_france is True
    assert listings[1].shipping_cost is None


def test_parse_product_offer_keeps_only_valid_individual_offers() -> None:
    html = PRODUCT_HTML.replace('data-price="319.99"', 'data-price="NaN"', 1).replace(
        'href="/product-offer/306753"', 'href="/product/google-family"'
    )

    assert parse_product_offers(html, "https://www.cashconverters.fr/product/example") == []


async def test_search_uses_family_results_only_to_fetch_individual_offers() -> None:
    search_html = """
    <section algolia-wrapper="Pixel8" data-application-id="APP123"
             data-api-admin-key="public-search-key" data-index-name="offers_fr"></section>
    """
    search_payload = {
        "results": [
            {
                "nbHits": 1,
                "hits": [
                    {
                        "objectID": "generic_8t",
                        "label": "Google Pixel8 5G Double SIM 128Go - Noir",
                        "slug": "google-pixel8-5g-double-sim-128go-noir-25536736",
                        "price": 1.23,
                        "nbOffer": 2,
                    }
                ],
            }
        ]
    }
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/p/search":
            return httpx.Response(200, text=search_html)
        if request.url.path == "/1/indexes/*/queries":
            body = json.loads(request.content)
            assert body["requests"][0]["params"].startswith("query=Pixel8")
            return httpx.Response(200, json=search_payload)
        if request.url.path.startswith("/product/google-pixel8"):
            return httpx.Response(200, text=PRODUCT_HTML)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        connector = CashConvertersConnector(client=client)
        listings = await connector.search_items("Pixel8", limit=2)

    assert len(listings) == 2
    assert {listing.listing_id for listing in listings} == {"306390", "306753"}
    assert all(listing.price == 319.99 for listing in listings)
    assert len(requests) == 3


async def test_search_skips_family_without_active_individual_offer() -> None:
    search_html = """
    <section algolia-wrapper="family" data-application-id="APP123"
             data-api-admin-key="public-search-key" data-index-name="offers_fr"></section>
    """
    payload = {
        "results": [
            {
                "nbHits": 1,
                "hits": [
                    {
                        "objectID": "family-only",
                        "label": "Prix à partir de",
                        "slug": "family-only-123",
                        "price": 99.99,
                        "nbOffer": 0,
                    }
                ],
            }
        ]
    }

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/p/search":
            return httpx.Response(200, text=search_html)
        if request.url.path == "/1/indexes/*/queries":
            return httpx.Response(200, json=payload)
        return httpx.Response(200, text="<html><h1>Family only</h1></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        connector = CashConvertersConnector(client=client)
        listings = await connector.search_items("family", limit=5)

    assert listings == []


async def test_search_fails_if_an_individual_product_page_cannot_be_verified() -> None:
    search_html = """
    <section algolia-wrapper="iphone" data-application-id="APP123"
             data-api-admin-key="public-search-key" data-index-name="offers_fr"></section>
    """
    payload = {
        "results": [
            {
                "hits": [
                    {"slug": "available-family", "nbOffer": 1},
                    {"slug": "failed-family", "nbOffer": 1},
                ]
            }
        ]
    }

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/p/search":
            return httpx.Response(200, text=search_html)
        if request.url.path == "/1/indexes/*/queries":
            return httpx.Response(200, json=payload)
        if request.url.path == "/product/available-family":
            return httpx.Response(200, text=PRODUCT_HTML)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        connector = CashConvertersConnector(client=client)
        with pytest.raises(RuntimeError, match="product detail fetch failed"):
            await connector.search_items("iphone", limit=3)


async def test_search_fails_closed_when_search_schema_changes() -> None:
    search_html = """
    <section algolia-wrapper="iphone" data-application-id="APP123"
             data-api-admin-key="public-search-key" data-index-name="offers_fr"></section>
    """

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/p/search":
            return httpx.Response(200, text=search_html)
        return httpx.Response(200, json={"results": [{"unexpected": []}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        connector = CashConvertersConnector(client=client)
        with pytest.raises(RuntimeError, match="search failed"):
            await connector.search_items("iphone", limit=3)

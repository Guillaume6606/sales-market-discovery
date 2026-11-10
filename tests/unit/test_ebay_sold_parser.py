from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ingestion.connectors import ebay_sold
from ingestion.connectors.ebay_sold import (
    build_sold_search_url,
    fetch_ebay_sold_page,
    parse_sold_date,
    parse_sold_search_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ebay_sold_sony_wh1000xm4.synthetic.html"


@pytest.fixture(scope="module")
def page() -> ebay_sold.SoldPage:
    return parse_sold_search_html(FIXTURE.read_text(encoding="utf-8"))


def _sale(page: ebay_sold.SoldPage, item_id: str) -> ebay_sold.SoldSale:
    return next(sale for sale in page.sales if sale.item_id == item_id)


def _rejection(page: ebay_sold.SoldPage, item_id: str) -> str:
    return next(card.reason for card in page.rejected if card.item_id == item_id)


def test_search_url_requests_completed_sold_items_only() -> None:
    url = build_sold_search_url("Sony WH-1000XM4")

    assert url.startswith("https://www.ebay.fr/sch/i.html?")
    assert "LH_Sold=1" in url and "LH_Complete=1" in url
    assert "_nkw=Sony+WH-1000XM4" in url


def test_parser_extracts_explicit_fields(page: ebay_sold.SoldPage) -> None:
    sale = _sale(page, "356001000001")

    assert sale.title == "Casque Sony WH-1000XM4 sans fil noir"
    assert sale.price_eur == Decimal("145.00")
    assert sale.currency == "EUR"
    assert sale.shipping_eur == Decimal("6.90")
    assert sale.sold_at == datetime(2026, 11, 3, tzinfo=UTC)
    assert sale.condition_raw == "Occasion"
    assert sale.url == "https://www.ebay.fr/itm/356001000001"


def test_parser_reads_free_shipping_as_explicit_zero(page: ebay_sold.SoldPage) -> None:
    assert _sale(page, "356001000002").shipping_eur == Decimal("0.00")


def test_parser_abstains_on_unspecified_shipping(page: ebay_sold.SoldPage) -> None:
    assert _sale(page, "356001000006").shipping_eur is None


def test_parser_abstains_on_missing_sale_date(page: ebay_sold.SoldPage) -> None:
    assert _sale(page, "356001000012").sold_at is None


def test_parser_handles_thousands_separator(page: ebay_sold.SoldPage) -> None:
    assert _sale(page, "356001000010").price_eur == Decimal("1199.00")


@pytest.mark.parametrize(
    ("item_id", "reason"),
    [
        ("356001000003", "best_offer_price_not_final"),
        ("356001000004", "price_range"),
        ("356001000005", "currency_not_eur_or_unparsed"),
    ],
)
def test_parser_rejects_prices_that_are_not_final_eur(
    page: ebay_sold.SoldPage, item_id: str, reason: str
) -> None:
    assert _rejection(page, item_id) == reason
    assert item_id not in {sale.item_id for sale in page.sales}


def test_parser_skips_placeholder_card(page: ebay_sold.SoldPage) -> None:
    ids = {sale.item_id for sale in page.sales} | {card.item_id for card in page.rejected}
    assert "123456" not in ids
    assert len(page.sales) == 13
    assert len(page.rejected) == 3


def test_struck_price_without_label_is_rejected() -> None:
    html = """
    <ul class="srp-results"><li class="s-item">
      <a class="s-item__link" href="https://www.ebay.fr/itm/987654321012?x=1">
        <div class="s-item__title"><span>Sony WH-1000XM4</span></div></a>
      <span class="s-item__caption--signal"><span>Vendu le 2 nov. 2026</span></span>
      <span class="s-item__price"><span class="STRIKETHROUGH POSITIVE">160,00 EUR</span></span>
    </li></ul>
    """

    page = parse_sold_search_html(html)

    assert page.sales == []
    assert page.rejected[0].reason == "struck_price_not_final"


def test_legacy_item_layout_is_parsed() -> None:
    html = """
    <ul class="srp-results"><li class="s-item">
      <span class="s-item__caption--signal POSITIVE"><span>Vendu le  2 nov. 2026</span></span>
      <a class="s-item__link" href="https://www.ebay.fr/itm/987654321013?x=1">
        <div class="s-item__title"><span>Sony WH-1000XM4</span></div></a>
      <div class="s-item__subtitle"><span class="SECONDARY_INFO">Occasion</span></div>
      <span class="s-item__price"><span class="POSITIVE">140,00 EUR</span></span>
      <span class="s-item__shipping s-item__logisticsCost">+5,00 EUR de frais de livraison</span>
    </li></ul>
    """

    sale = parse_sold_search_html(html).sales[0]

    assert (sale.item_id, sale.price_eur, sale.shipping_eur) == (
        "987654321013",
        Decimal("140.00"),
        Decimal("5.00"),
    )
    assert sale.sold_at == datetime(2026, 11, 2, tzinfo=UTC)
    assert sale.condition_raw == "Occasion"


def test_unrecognized_page_is_flagged() -> None:
    page = parse_sold_search_html("<html><title>Error Page | eBay</title></html>")

    assert page.layout_recognized is False
    assert page.sales == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vendu le 3 févr. 2026", datetime(2026, 2, 3, tzinfo=UTC)),
        ("Vendu le 14 août 2026", datetime(2026, 8, 14, tzinfo=UTC)),
        ("Vendu le 31 févr. 2026", None),
        ("Terminé", None),
    ],
)
def test_sold_date_parser_abstains_when_unparseable(text: str, expected: datetime | None) -> None:
    assert parse_sold_date(text) == expected


class _Response:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _Session:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.calls: list[str] = []

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get_with_retry(self, url: str) -> _Response:
        self.calls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


async def test_fetch_skips_network_during_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = AsyncMock()
    redis.exists.return_value = 1
    session = _Session(_Response(200, FIXTURE.read_text(encoding="utf-8")))
    monkeypatch.setattr(ebay_sold, "ScrapingSession", lambda _config: session)

    result = await fetch_ebay_sold_page("Sony WH-1000XM4", redis)

    assert result.status == "cooldown"
    assert session.calls == []


async def test_fetch_denial_starts_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = AsyncMock()
    redis.exists.return_value = 0
    session = _Session(RuntimeError("Bot detection detected after all retries"))
    monkeypatch.setattr(ebay_sold, "ScrapingSession", lambda _config: session)

    result = await fetch_ebay_sold_page("Sony WH-1000XM4", redis)

    assert result.status == "blocked"
    assert len(session.calls) == 1
    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[0] == "ingestion:cooldown:ebay_sold"


async def test_fetch_parses_ok_page(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = AsyncMock()
    redis.exists.return_value = 0
    session = _Session(_Response(200, FIXTURE.read_text(encoding="utf-8")))
    monkeypatch.setattr(ebay_sold, "ScrapingSession", lambda _config: session)

    result = await fetch_ebay_sold_page("Sony WH-1000XM4", redis)

    assert result.status == "ok"
    assert result.page is not None and len(result.page.sales) == 13

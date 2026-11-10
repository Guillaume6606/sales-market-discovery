"""eBay.fr completed-sales search page: bounded fetch and abstaining parser.

The parser returns a value only when the page states it explicitly. Ambiguous prices
(best offer accepted, ranges, non-EUR) reject the card; unknown shipping or sale date
stay ``None`` rather than being guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from loguru import logger

from libs.common.cooldown import set_source_cooldown, source_cooldown_active
from libs.common.scraping import ScrapingConfig, ScrapingSession
from libs.common.settings import settings

COOLDOWN_SOURCE = "ebay_sold"
SEARCH_ENDPOINT = "https://www.ebay.fr/sch/i.html"
PAGE_SIZE = 60

_PLACEHOLDER_TITLES = {"achetez sur ebay", "shop on ebay"}
_TITLE_SUFFIXES = (
    "la page s'ouvre dans une nouvelle fenêtre ou un nouvel onglet",
    "opens in a new window or tab",
)
_FRENCH_MONTHS = {
    "janv": 1,
    "janvier": 1,
    "févr": 2,
    "fevr": 2,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avr": 4,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "sept": 9,
    "septembre": 9,
    "oct": 10,
    "octobre": 10,
    "nov": 11,
    "novembre": 11,
    "déc": 12,
    "dec": 12,
    "décembre": 12,
    "decembre": 12,
}
_SOLD_DATE = re.compile(r"vendu\s+le\s+(\d{1,2})\s+([a-zéèûô]+)\.?\s+(\d{4})", re.IGNORECASE)
_AMOUNT = re.compile(r"\d{1,3}(?:[   .]\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?")
_EUR_PRICE = re.compile(
    r"^\s*(?P<amount>\d{1,3}(?:[   .]\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)"
    r"\s*(?:EUR|€)\s*$"
)
_SHIPPING_COST = re.compile(
    r"^\+\s*(?P<amount>\d{1,3}(?:[   .]\d{3})*(?:,\d{2})?|\d+(?:,\d{2})?)"
    r"\s*(?:EUR|€)\s+de\s+frais\s+de\s+livraison$",
    re.IGNORECASE,
)
_FREE_SHIPPING = re.compile(r"^livraison\s+gratuite$", re.IGNORECASE)
_BEST_OFFER = re.compile(r"meilleure\s+offre\s+accept|best\s+offer\s+accepted", re.IGNORECASE)
_ITEM_PATH = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,15})(?:[/?#]|$)")


@dataclass(frozen=True)
class SoldSale:
    item_id: str
    title: str
    price_eur: Decimal
    currency: str
    shipping_eur: Decimal | None
    sold_at: datetime | None
    condition_raw: str | None
    url: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "price_eur": str(self.price_eur),
            "currency": self.currency,
            "shipping_eur": None if self.shipping_eur is None else str(self.shipping_eur),
            "sold_at": None if self.sold_at is None else self.sold_at.isoformat(),
            "condition_raw": self.condition_raw,
            "url": self.url,
        }


@dataclass(frozen=True)
class RejectedCard:
    item_id: str | None
    title: str | None
    reason: str
    raw_price: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "reason": self.reason,
            "raw_price": self.raw_price,
        }


@dataclass
class SoldPage:
    layout_recognized: bool
    sales: list[SoldSale] = field(default_factory=list)
    rejected: list[RejectedCard] = field(default_factory=list)


@dataclass
class SoldFetchResult:
    status: str
    url: str
    page: SoldPage | None = None
    error: str | None = None


def build_sold_search_url(query: str) -> str:
    params = {"_nkw": query, "LH_Sold": "1", "LH_Complete": "1", "_ipg": str(PAGE_SIZE)}
    return f"{SEARCH_ENDPOINT}?{urlencode(params)}"


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _parse_amount(raw: str) -> Decimal | None:
    normalized = re.sub(r"[   .]", "", raw).replace(",", ".")
    try:
        value = Decimal(normalized)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def parse_sold_date(text: str) -> datetime | None:
    match = _SOLD_DATE.search(text)
    if not match:
        return None
    month = _FRENCH_MONTHS.get(match.group(2).casefold())
    if month is None:
        return None
    try:
        return datetime(int(match.group(3)), month, int(match.group(1)), tzinfo=UTC)
    except ValueError:
        return None


def _title(card: Tag) -> str:
    heading = card.select_one(".s-card__title") or card.select_one(".s-item__title")
    if heading is None:
        return ""
    for clipped in heading.select(".clipped"):
        clipped.decompose()
    title = _text(heading)
    for suffix in _TITLE_SUFFIXES:
        if title.casefold().endswith(suffix):
            title = title[: -len(suffix)].strip()
    return title


def _item_url(card: Tag) -> tuple[str | None, str | None]:
    for link in card.select("a[href]"):
        href = str(link.get("href") or "")
        match = _ITEM_PATH.search(href)
        if match:
            parts = urlsplit(href)
            return match.group(1), urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return None, None


def _condition(card: Tag) -> str | None:
    subtitle = card.select_one(".s-card__subtitle") or card.select_one(".s-item__subtitle")
    if subtitle is None:
        return None
    first = subtitle.find("span")
    value = _text(first if isinstance(first, Tag) else subtitle)
    return value or None


def _price_nodes(card: Tag) -> list[Tag]:
    return card.select(".s-card__price") or card.select(".s-item__price")


def _is_struck(node: Tag) -> bool:
    for element in [node, *node.find_all(True)]:
        classes = {value.casefold() for value in (element.get("class") or [])}
        if "strikethrough" in classes:
            return True
    return node.find(["s", "del", "strike"]) is not None


def _shipping(card: Tag) -> Decimal | None:
    candidates = card.select(".s-card__attribute-row") or card.select(
        ".s-item__shipping, .s-item__logisticsCost"
    )
    values: set[Decimal] = set()
    for candidate in candidates:
        text = _text(candidate)
        if _FREE_SHIPPING.match(text):
            values.add(Decimal("0.00"))
            continue
        match = _SHIPPING_COST.match(text)
        if match:
            amount = _parse_amount(match.group("amount"))
            if amount is not None:
                values.add(amount)
    return values.pop() if len(values) == 1 else None


def _parse_card(card: Tag) -> SoldSale | RejectedCard | None:
    title = _title(card)
    if title.casefold() in _PLACEHOLDER_TITLES:
        return None
    item_id, url = _item_url(card)
    if item_id is None or url is None:
        return RejectedCard(None, title or None, "item_url_missing")
    if not title:
        return RejectedCard(item_id, None, "title_missing")

    price_nodes = _price_nodes(card)
    raw_price = " | ".join(_text(node) for node in price_nodes) or None
    if _BEST_OFFER.search(_text(card)):
        return RejectedCard(item_id, title, "best_offer_price_not_final", raw_price)
    if any(_is_struck(node) for node in price_nodes):
        return RejectedCard(item_id, title, "struck_price_not_final", raw_price)
    if len(price_nodes) != 1:
        return RejectedCard(item_id, title, "price_missing_or_ambiguous", raw_price)
    price_text = _text(price_nodes[0])
    if len(_AMOUNT.findall(price_text)) > 1 or re.search(r"\s(?:à|to)\s", price_text):
        return RejectedCard(item_id, title, "price_range", raw_price)
    match = _EUR_PRICE.match(price_text)
    if match is None:
        return RejectedCard(item_id, title, "currency_not_eur_or_unparsed", raw_price)
    price = _parse_amount(match.group("amount"))
    if price is None:
        return RejectedCard(item_id, title, "price_invalid", raw_price)

    caption = card.select_one(".s-card__caption") or card.select_one(".s-item__caption--signal")
    return SoldSale(
        item_id=item_id,
        title=title,
        price_eur=price.quantize(Decimal("0.01")),
        currency="EUR",
        shipping_eur=_shipping(card),
        sold_at=parse_sold_date(_text(caption)),
        condition_raw=_condition(card),
        url=url,
    )


def parse_sold_search_html(html: str) -> SoldPage:
    soup = BeautifulSoup(html, "html.parser")
    results = soup.select_one("ul.srp-results")
    if results is None:
        return SoldPage(layout_recognized=False)
    page = SoldPage(layout_recognized=True)
    seen: set[str] = set()
    for card in results.select("li.s-card, li.s-item"):
        parsed = _parse_card(card)
        if parsed is None:
            continue
        if isinstance(parsed, RejectedCard):
            page.rejected.append(parsed)
            continue
        if parsed.item_id in seen:
            page.rejected.append(RejectedCard(parsed.item_id, parsed.title, "duplicate_item"))
            continue
        seen.add(parsed.item_id)
        page.sales.append(parsed)
    return page


def _session_config() -> ScrapingConfig:
    config = ScrapingConfig()
    config.use_playwright = False
    config.max_retries = 1
    config.min_delay = settings.sold_comps_min_delay_seconds
    config.max_delay = settings.sold_comps_min_delay_seconds * 2
    return config


async def fetch_ebay_sold_page(query: str, redis: Any) -> SoldFetchResult:
    """Fetch one eBay.fr completed-sales page; a denial starts the shared source cooldown."""
    url = build_sold_search_url(query)
    if await source_cooldown_active(redis, COOLDOWN_SOURCE):
        return SoldFetchResult(status="cooldown", url=url)
    try:
        async with ScrapingSession(_session_config()) as session:
            response = await session.get_with_retry(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("eBay sold search denied or failed for '{}': {}", query, exc)
        await set_source_cooldown(redis, COOLDOWN_SOURCE, settings.sold_comps_cooldown_seconds)
        return SoldFetchResult(status="blocked", url=url, error=type(exc).__name__)
    if response.status_code >= 400:
        await set_source_cooldown(redis, COOLDOWN_SOURCE, settings.sold_comps_cooldown_seconds)
        return SoldFetchResult(status="blocked", url=url, error=f"http_{response.status_code}")
    page = parse_sold_search_html(response.text)
    if not page.layout_recognized:
        return SoldFetchResult(status="unrecognized_page", url=url, page=page)
    return SoldFetchResult(status="ok", url=url, page=page)

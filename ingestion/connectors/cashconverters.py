"""Cash Converters connector using its public storefront search and product pages."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import urlencode, urljoin

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from libs.common.condition import normalize_condition
from libs.common.models import Listing

BASE_URL = "https://www.cashconverters.fr"
SEARCH_URL = f"{BASE_URL}/p/search"
MAX_PRODUCT_PAGES = 20
_OFFER_PATH_RE = re.compile(r"^/product-offer/(?P<offer_id>[0-9]+)$")
_SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_CONFIG_VALUE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
NormalizedCondition = Literal["new", "like_new", "good", "fair"]


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _parse_positive_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def _delivery_evidence(soup: BeautifulSoup) -> tuple[bool | None, str | None]:
    delivery = soup.select_one(".foldable-content.delivery")
    if delivery is None:
        return None, None

    text = _clean_text(delivery.get_text(" ", strip=True)).casefold()
    if "livraison à domicile" in text:
        return (
            True,
            "Fiche produit contenant l'offre: livraison à domicile; politique Cash Converters: "
            "France métropolitaine uniquement",
        )
    if "retrait en magasin uniquement" in text:
        return False, "Offre indiquée en retrait en magasin uniquement"
    return None, None


def _offer_delivery_evidence(
    option_text: str,
    *,
    is_selected: bool,
    page_evidence: tuple[bool | None, str | None],
) -> tuple[bool | None, str | None]:
    folded = option_text.casefold()
    if "livraison offerte" in folded or "livraison à domicile" in folded:
        return True, "Offre individuelle indiquée disponible en livraison"
    if "retrait en magasin uniquement" in folded:
        return False, "Offre individuelle indiquée en retrait en magasin uniquement"
    if is_selected:
        return page_evidence
    return None, None


def parse_product_offers(
    html: str,
    product_url: str,
    *,
    observed_at: datetime | None = None,
) -> list[Listing]:
    """Parse active individual offers from one Cash Converters product page."""
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one(".product-block h1") or soup.select_one("h1")
    title = _clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
    if not title:
        return []

    page_delivery_evidence = _delivery_evidence(soup)
    timestamp = observed_at or datetime.now(UTC)
    results: list[Listing] = []
    seen_offer_ids: set[str] = set()

    for option in soup.select(".product-offer .options a.option[href]"):
        href = str(option.get("href") or "")
        match = _OFFER_PATH_RE.fullmatch(href)
        if match is None:
            continue

        offer_id = match.group("offer_id")
        if offer_id in seen_offer_ids:
            continue

        price = _parse_positive_price(option.get("data-price"))
        option_text = _clean_text(option.get_text(" ", strip=True))
        if price is None or "€" not in option_text:
            continue

        condition_raw = _clean_text(str(option.get("data-state-label") or ""))
        if not condition_raw:
            continue

        store_node = option.select_one(".store-name")
        location = _clean_text(store_node.get_text(" ", strip=True)) if store_node else None
        shipping_cost = 0.0 if "livraison offerte" in option_text.casefold() else None
        delivery_to_france, delivery_evidence = _offer_delivery_evidence(
            option_text,
            is_selected="selected" in (option.get("class") or []),
            page_evidence=page_delivery_evidence,
        )

        results.append(
            Listing(
                source="cashconverters",
                listing_id=offer_id,
                title=title,
                price=price,
                currency="EUR",
                condition_raw=condition_raw,
                condition_norm=cast(NormalizedCondition | None, normalize_condition(condition_raw)),
                location=location or None,
                seller_rating=None,
                shipping_cost=shipping_cost,
                delivery_to_france=delivery_to_france,
                delivery_evidence=delivery_evidence,
                observed_at=timestamp,
                is_sold=False,
                url=urljoin(product_url, href),
            )
        )
        seen_offer_ids.add(offer_id)

    return results


def _parse_search_config(html: str) -> tuple[str, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    wrapper = soup.select_one("[algolia-wrapper]")
    if wrapper is None:
        raise ValueError("Cash Converters search configuration is missing")

    values = (
        str(wrapper.get("data-application-id") or ""),
        str(wrapper.get("data-api-admin-key") or ""),
        str(wrapper.get("data-index-name") or ""),
    )
    if not all(value and _CONFIG_VALUE_RE.fullmatch(value) for value in values):
        raise ValueError("Cash Converters search configuration is invalid")
    return values


def _product_slugs(payload: dict[str, Any]) -> list[str]:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("Cash Converters search results are missing")
    first_result = results[0]
    if not isinstance(first_result, dict):
        raise ValueError("Cash Converters search result is invalid")
    hits = first_result.get("hits")
    if not isinstance(hits, list):
        raise ValueError("Cash Converters search hits are missing")

    slugs: list[str] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        nb_offer = hit.get("nbOffer")
        if isinstance(nb_offer, (int, float)) and nb_offer <= 0:
            continue
        slug = hit.get("slug")
        if isinstance(slug, str) and _SLUG_RE.fullmatch(slug) and slug not in slugs:
            slugs.append(slug)
    return slugs


class CashConvertersConnector:
    """Extract individual professional-store offers from Cash Converters France."""

    SOURCE = "cashconverters"

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def search_items(self, keyword: str, limit: int = 50) -> list[Listing]:
        """Search product families, then resolve their active individual offers."""
        keyword = _clean_text(keyword)
        if limit <= 0:
            return []
        if not keyword:
            raise ValueError("keyword must not be empty")

        if self._client is not None:
            return await self._search(self._client, keyword, limit)

        headers = {"User-Agent": "sales-market-discovery/1.0 (public storefront reader)"}
        async with httpx.AsyncClient(
            headers=headers, timeout=30.0, follow_redirects=True
        ) as client:
            return await self._search(client, keyword, limit)

    async def _search(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        limit: int,
    ) -> list[Listing]:
        try:
            search_page = await client.get(SEARCH_URL, params={"q": keyword})
            search_page.raise_for_status()
            application_id, api_key, index_name = _parse_search_config(search_page.text)

            family_limit = min(max(limit, 1), MAX_PRODUCT_PAGES)
            params = urlencode({"query": keyword, "hitsPerPage": family_limit})
            endpoint = f"https://{application_id}-dsn.algolia.net/1/indexes/*/queries"
            response = await client.post(
                endpoint,
                headers={
                    "x-algolia-application-id": application_id,
                    "x-algolia-api-key": api_key,
                },
                json={"requests": [{"indexName": index_name, "params": params}]},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Cash Converters search returned an invalid payload")
            product_slugs = _product_slugs(payload)
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("Cash Converters search failed: {}", type(exc).__name__)
            raise RuntimeError("Cash Converters search failed") from exc

        listings: list[Listing] = []
        detail_errors: list[httpx.HTTPError] = []
        for slug in product_slugs[:MAX_PRODUCT_PAGES]:
            product_url = f"{BASE_URL}/product/{slug}"
            try:
                product_response = await client.get(product_url)
                product_response.raise_for_status()
            except httpx.HTTPError as exc:
                detail_errors.append(exc)
                logger.warning(
                    "Skipping Cash Converters product page after {}",
                    type(exc).__name__,
                )
                continue

            listings.extend(parse_product_offers(product_response.text, product_url))
            if len(listings) >= limit:
                break

        if detail_errors:
            raise RuntimeError("Cash Converters product detail fetch failed") from detail_errors[0]
        return listings[:limit]


async def fetch_cashconverters_listings(query: str, limit: int = 50) -> list[Listing]:
    """Fetch Cash Converters offers through the standard ingestion function contract."""
    return await CashConvertersConnector().search_items(query, limit)

"""Vinted connector backed by the ``vinted-scraper`` REST API package."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from libs.common.models import Listing
from libs.common.scraping import ScrapingUtils


class VintedAPIConnector:
    """Wrapper around ``AsyncVintedScraper`` that returns project ``Listing`` models.

    The ``vinted-scraper`` package calls ``/api/v2/catalog/items`` with automatic
    cookie management via ``httpx``, bypassing DataDome's web protection for search.
    """

    BASE_URL = "https://www.vinted.fr"
    SOURCE = "vinted"

    def __init__(self) -> None:
        self._scraping_utils = ScrapingUtils()

    async def search_items(self, keyword: str, limit: int = 50) -> list[Listing]:
        """Search Vinted via the REST API and return ``Listing`` objects.

        Parameters
        ----------
        keyword:
            Free-text search query.
        limit:
            Maximum number of results to return.
        """
        if limit <= 0:
            return []

        from vinted_scraper import AsyncVintedScraper

        results: list[Listing] = []

        try:
            scraper = await AsyncVintedScraper.create(self.BASE_URL)

            async with scraper:
                params: dict[str, Any] = {
                    "search_text": keyword,
                    "per_page": min(limit, 96),
                    "order": "newest_first",
                }

                items = await scraper.search(params)

                for item in items:
                    # VintedItem exposes json_data with the raw API dict
                    raw = getattr(item, "json_data", None) or {}
                    listing = self._map_item_to_listing(raw)
                    if listing:
                        results.append(listing)
                        if len(results) >= limit:
                            break

        except Exception as exc:
            logger.error(f"Vinted API search failed for '{keyword}': {exc}")

        return results

    def _map_item_to_listing(self, data: dict[str, Any]) -> Listing | None:
        """Map a raw Vinted API item dict to a project ``Listing``.

        Handles the various price representations returned by the API
        (dict with amount/currency_code, float, int, or str).
        """
        item_id = data.get("id")
        if not item_id:
            logger.debug("Skipping Vinted item without an id")
            return None

        title = data.get("title") or ""
        price_value, currency = self._extract_price(data)
        url = self._build_url(data.get("url"))

        is_sold = bool(data.get("is_closed") or data.get("is_reserved"))

        condition_raw = data.get("status") or None
        location = data.get("localization") or None
        brand = data.get("brand_title") or None
        size = data.get("size_title") or None
        color = data.get("color1") or None

        return Listing(
            source=self.SOURCE,
            listing_id=str(item_id),
            title=self._scraping_utils.clean_text(title),
            price=price_value,
            currency=currency,
            condition_raw=condition_raw,
            condition_norm=self.normalize_condition_vinted(condition_raw or ""),
            location=location,
            seller_rating=None,
            shipping_cost=None,
            observed_at=datetime.now(UTC),
            is_sold=is_sold,
            url=url,
            brand=brand,
            size=size,
            color=color,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_price(self, data: dict[str, Any]) -> tuple[float | None, str]:
        """Extract price and currency from various API representations."""
        raw_price = data.get("price")

        if isinstance(raw_price, dict):
            amount_str = raw_price.get("amount")
            currency = raw_price.get("currency_code") or "EUR"
            if amount_str is not None:
                try:
                    return float(amount_str), currency
                except (ValueError, TypeError):
                    return None, currency
            return None, currency

        if isinstance(raw_price, (int, float)):
            return float(raw_price), data.get("currency") or "EUR"

        if isinstance(raw_price, str):
            try:
                return float(raw_price), data.get("currency") or "EUR"
            except ValueError:
                parsed = self._scraping_utils.extract_price(raw_price)
                return parsed, data.get("currency") or "EUR"

        return None, data.get("currency") or "EUR"

    def _build_url(self, raw_url: str | None) -> str | None:
        """Prefix relative URLs with BASE_URL."""
        if not raw_url:
            return None
        if raw_url.startswith("http"):
            return raw_url
        return f"{self.BASE_URL}{raw_url}"

    @staticmethod
    def normalize_condition_vinted(condition_raw: str) -> str | None:
        """Normalize Vinted condition strings to standard categories.

        Mirrors ``VintedConnector.normalize_condition_vinted`` so both code-paths
        produce identical condition_norm values.
        """
        if not condition_raw:
            return None

        condition_lower = condition_raw.lower()

        if any(
            word in condition_lower
            for word in [
                "neuf avec étiquette",
                "neuf sans étiquette",
                "neuf",
                "new",
                "nouveau",
                "brand new",
            ]
        ):
            return "new"
        if any(
            word in condition_lower
            for word in ["très bon état", "very good", "excellent", "comme neuf", "like new"]
        ):
            return "like_new"
        if any(word in condition_lower for word in ["bon état", "good", "bien", "used"]):
            return "good"
        if any(word in condition_lower for word in ["satisfaisant", "fair", "acceptable", "worn"]):
            return "fair"

        return None


async def fetch_vinted_api_listings(keyword: str, limit: int = 50) -> list[Listing]:
    """Convenience function: search Vinted via the REST API."""
    connector = VintedAPIConnector()
    return await connector.search_items(keyword, limit=limit)

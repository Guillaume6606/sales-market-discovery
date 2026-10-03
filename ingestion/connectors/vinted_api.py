"""Vinted connector backed by the ``vinted-scraper`` REST API package."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger
from redis.asyncio import Redis

from ingestion.connectors.vinted_transport import ReliableVintedScraper
from libs.common.condition import normalize_condition
from libs.common.cooldown import set_source_cooldown, source_cooldown_active
from libs.common.models import Listing
from libs.common.scraping import ScrapingUtils
from libs.common.settings import settings

if TYPE_CHECKING:
    from libs.common.models import ListingDetail


COOLDOWN_SECONDS = 300
_redis: Redis | None = None


def _get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    return _redis


class VintedAPIConnector:
    """Wrapper around ``AsyncVintedScraper`` that returns project ``Listing`` models.

    The ``vinted-scraper`` package calls ``/api/v2/catalog/items`` with automatic
    cookie management via ``httpx``. Access remains subject to Vinted protection.
    """

    BASE_URL = "https://www.vinted.fr"
    SOURCE = "vinted"
    _cooldown_until = 0.0

    def __init__(self) -> None:
        self._scraping_utils = ScrapingUtils()

    async def _cooldown_active(self) -> bool:
        if monotonic() < self._cooldown_until:
            return True
        try:
            return await source_cooldown_active(_get_redis(), self.SOURCE)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vinted cooldown lookup failed: {}", type(exc).__name__)
            return False

    async def _start_cooldown(self) -> None:
        type(self)._cooldown_until = monotonic() + COOLDOWN_SECONDS
        try:
            await set_source_cooldown(_get_redis(), self.SOURCE, COOLDOWN_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vinted cooldown persistence failed: {}", type(exc).__name__)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ReliableVintedScraper]:
        if await self._cooldown_active():
            raise RuntimeError("Vinted cooldown after access denial or rate limit")
        config: dict[str, Any] = {"timeout": 30.0}
        if settings.scraping_proxy_url:
            config["proxy"] = settings.scraping_proxy_url
        async with ReliableVintedScraper(self.BASE_URL, config=config) as scraper:
            try:
                scraper.session_cookie = await scraper.refresh_cookie()
                yield scraper
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 403, 429}:
                    await self._start_cooldown()
                raise

    @staticmethod
    def _failure_reason(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.TransportError):
            return type(exc).__name__
        if isinstance(exc, RuntimeError) and str(exc).startswith("Vinted cooldown"):
            return "cooldown after access denial or rate limit"
        return type(exc).__name__

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

        logger.info("Searching Vinted API for: {}", keyword)

        results: list[Listing] = []

        try:
            async with self._session() as scraper:
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
            reason = self._failure_reason(exc)
            logger.error("Vinted API search failed: {}", reason)
            raise RuntimeError(f"Vinted API search failed: {reason}") from None

        return results

    async def fetch_detail(self, listing_id: str, obs_id: int) -> ListingDetail | None:
        """Fetch detailed data for a single Vinted listing.

        Uses ``AsyncVintedScraper.item()`` with cookie-based authentication.
        Vinted items are always shipped (``local_pickup_only=False``) and
        offers are always enabled (``negotiation_enabled=True``).

        Args:
            listing_id: The Vinted item ID (numeric string).
            obs_id: The observation ID to associate with the detail record.

        Returns:
            A populated ``ListingDetail`` or ``None`` if the fetch fails.

        Note:
            The ``/api/v2/items/:id`` endpoint may return 403 after a few
            requests due to DataDome rate limiting.  Failures are logged and
            ``None`` is returned rather than raising.
        """
        from libs.common.models import ListingDetail

        try:
            async with self._session() as scraper:
                item = await scraper.item(listing_id)
        except Exception as exc:
            logger.warning("Vinted item fetch failed: {}", self._failure_reason(exc))
            return None

        if item is None:
            return None

        description: str | None = item.description or None

        # Collect photo URLs from the photos list (prefer full_size_url, fall back to url)
        photo_urls: list[str] = []
        for photo in item.photos or []:
            url = photo.full_size_url or photo.url
            if url:
                photo_urls.append(url)

        # original_posted_at — not directly on VintedItem; fall back to json_data
        original_posted_at: datetime | None = None
        raw_created = (item.json_data or {}).get("created_at_ts") or (item.json_data or {}).get(
            "created_at"
        )
        if raw_created:
            try:
                if isinstance(raw_created, (int, float)):
                    original_posted_at = datetime.fromtimestamp(raw_created, tz=UTC)
                else:
                    original_posted_at = datetime.fromisoformat(
                        str(raw_created).replace("Z", "+00:00")
                    )
            except (ValueError, TypeError, OSError):
                pass

        # Seller info from VintedUser
        seller_account_age_days: int | None = None
        seller_transaction_count: int | None = None

        favourite_count = item.favourite_count
        view_count = item.view_count

        return ListingDetail(
            obs_id=obs_id,
            description=description,
            photo_urls=photo_urls,
            original_posted_at=original_posted_at,
            seller_account_age_days=seller_account_age_days,
            seller_transaction_count=seller_transaction_count,
            view_count=view_count,
            favorite_count=favourite_count,
            local_pickup_only=False,  # Vinted is always shipped
            negotiation_enabled=True,  # Vinted always allows offers
        )

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

        if data.get("is_closed") or data.get("is_reserved"):
            return None

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
            condition_norm=normalize_condition(condition_raw or ""),
            location=location,
            seller_rating=None,
            shipping_cost=None,
            observed_at=datetime.now(UTC),
            is_sold=False,
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


async def fetch_vinted_api_listings(keyword: str, limit: int = 50) -> list[Listing]:
    """Convenience function: search Vinted via the REST API."""
    connector = VintedAPIConnector()
    return await connector.search_items(keyword, limit=limit)

"""LeBonCoin connector backed by the public lbc package."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import lbc
from loguru import logger

from libs.common.models import Listing
from libs.common.scraping import ScrapingUtils


class LeBonCoinAPIConnector:
    """Wrapper around ``lbc.Client`` that returns project ``Listing`` models."""

    MAX_PAGE_SIZE = 35
    SOURCE = "leboncoin"

    def __init__(
        self,
        *,
        client: Optional[lbc.Client] = None,
        proxy: Optional[lbc.Proxy] = None,
    ) -> None:
        # Allow dependency injection for easier testing while still supporting proxy configuration.
        if client is not None:
            self._client = client
        elif proxy is not None:
            self._client = lbc.Client(proxy=proxy)
        else:
            self._client = lbc.Client()

        self._scraping_utils = ScrapingUtils()

    async def search_items(
        self,
        *,
        keyword: Optional[str] = None,
        limit: int = 50,
        url: Optional[str] = None,
        locations: Optional[Iterable[lbc.City | lbc.Department | lbc.Region]] = None,
        sort: lbc.Sort = lbc.Sort.NEWEST,
        ad_type: lbc.AdType = lbc.AdType.OFFER,
        owner_type: lbc.OwnerType = lbc.OwnerType.ALL,
        extra_filters: Optional[Dict[str, Any]] = None,
    ) -> List[Listing]:
        """Search active listings by delegating to ``lbc.Client.search``.

        Parameters mirror ``lbc.Client.search``. Either ``keyword`` or ``url`` must be provided.
        ``extra_filters`` lets callers forward less-common arguments (e.g., price ranges).
        """

        if limit <= 0:
            return []

        if not keyword and not url:
            raise ValueError("Either keyword or url must be provided")

        per_page = min(max(limit, 1), self.MAX_PAGE_SIZE)
        remaining = limit
        page = 1
        results: List[Listing] = []

        filters: Dict[str, Any] = {
            "page": page,
            "limit": per_page,
            "sort": sort,
            "ad_type": ad_type,
            "owner_type": owner_type,
        }

        if keyword:
            filters["text"] = keyword
        if url:
            filters["url"] = url
        if locations:
            filters["locations"] = list(locations)
        if extra_filters:
            for key, value in extra_filters.items():
                if key in {"limit", "page"}:
                    continue
                filters[key] = value

        while remaining > 0:
            filters["page"] = page

            try:
                raw_response = await asyncio.to_thread(self._client.search, **filters)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error(f"LeBonCoin API search failed (page={page}): {exc}")
                break

            ads = list(getattr(raw_response, "ads", []) or [])
            if not ads:
                break

            for ad in ads:
                listing = self._map_ad_to_listing(ad)
                if listing:
                    results.append(listing)
                    remaining -= 1
                    if remaining == 0:
                        break

            if len(ads) < per_page:
                break

            page += 1

        return results

    def _map_ad_to_listing(self, ad: Any) -> Optional[Listing]:
        ad_data = self._ad_to_dict(ad)
        listing_id = self._extract_listing_id(ad, ad_data)
        if not listing_id:
            logger.debug("Skipping ad without an identifier")
            return None

        title = self._extract_field(ad, ad_data, ["subject", "title", "name"]) or ""
        price_value, currency = self._extract_price(ad, ad_data)

        description = self._extract_field(ad, ad_data, ["body", "description", "content"])
        condition_raw = self._extract_field(ad, ad_data, ["condition", "item_condition", "state"]) or None
        location = self._extract_location(ad_data)
        shipping_cost = self._extract_shipping_cost(ad_data)
        url = self._extract_field(ad, ad_data, ["url", "permalink", "short_url"])

        return Listing(
            source=self.SOURCE,
            listing_id=str(listing_id),
            title=self._scraping_utils.clean_text(title),
            price=price_value,
            currency=currency or "EUR",
            condition_raw=condition_raw,
            condition_norm=self.normalize_condition_leboncoin(condition_raw or ""),
            location=location,
            seller_rating=None,
            shipping_cost=shipping_cost,
            observed_at=datetime.now(timezone.utc),
            is_sold=False,
            url=url,
            brand=None,
            size=None,
            color=None,
        )

    @staticmethod
    def normalize_condition_leboncoin(condition_raw: str) -> Optional[str]:
        if not condition_raw:
            return None

        condition_lower = condition_raw.lower()
        normalized = (
            condition_lower.replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("à", "a")
        )

        if any(word in normalized for word in ["neuf", "new", "nouveau"]):
            return "new"
        if any(word in normalized for word in ["tres bon etat", "comme neuf", "like new", "excellent"]):
            return "like_new"
        if any(word in normalized for word in ["bon etat", "good", "bien"]):
            return "good"
        if any(word in normalized for word in ["satisfaisant", "fair", "acceptable"]):
            return "fair"

        return None

    @staticmethod
    def _ad_to_dict(ad: Any) -> Dict[str, Any]:
        if isinstance(ad, dict):
            return ad

        for attr_name in ("model_dump", "dict"):
            attr = getattr(ad, attr_name, None)
            if callable(attr):
                try:
                    return attr()
                except TypeError:
                    try:
                        return attr(exclude_none=True)
                    except TypeError:
                        continue

        if hasattr(ad, "__dict__") and isinstance(ad.__dict__, dict):
            return dict(ad.__dict__)

        return {}

    @staticmethod
    def _extract_field(ad: Any, ad_data: Dict[str, Any], keys: Iterable[str]) -> Optional[Any]:
        for key in keys:
            if key in ad_data and ad_data[key] not in (None, ""):
                return ad_data[key]
            if hasattr(ad, key):
                value = getattr(ad, key)
                if value not in (None, ""):
                    return value
        return None

    def _extract_price(self, ad: Any, ad_data: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
        currency = ad_data.get("currency") or ad_data.get("price_currency") or "EUR"
        price_info = ad_data.get("price")

        if isinstance(price_info, dict):
            for key in ("amount", "value", "price", "raw"):
                if key in price_info and price_info[key] not in (None, ""):
                    value = price_info[key]
                    if isinstance(value, (int, float)):
                        return float(value), price_info.get("currency") or currency
                    if isinstance(value, str):
                        parsed = self._scraping_utils.extract_price(value)
                        if parsed is not None:
                            return parsed, price_info.get("currency") or currency

        if isinstance(price_info, (int, float)):
            return float(price_info), currency

        if isinstance(price_info, str):
            parsed = self._scraping_utils.extract_price(price_info)
            if parsed is not None:
                return parsed, currency

        # Fallback to attribute access if not already covered.
        attr_price = self._extract_field(ad, ad_data, ["price_value", "price_amount"])
        if isinstance(attr_price, (int, float)):
            return float(attr_price), currency

        if isinstance(attr_price, str):
            parsed = self._scraping_utils.extract_price(attr_price)
            if parsed is not None:
                return parsed, currency

        return None, currency

    def _extract_location(self, ad_data: Dict[str, Any]) -> Optional[str]:
        location_info = ad_data.get("location")
        if isinstance(location_info, dict):
            parts = [
                location_info.get("city"),
                location_info.get("zipcode") or location_info.get("postal_code"),
            ]
            location = " ".join(filter(None, parts)).strip()
            if location:
                return self._scraping_utils.clean_text(location)

        city = ad_data.get("city") or ad_data.get("location_label")
        if isinstance(city, str) and city.strip():
            return self._scraping_utils.clean_text(city)

        return None

    def _extract_shipping_cost(self, ad_data: Dict[str, Any]) -> Optional[float]:
        shipping_info = ad_data.get("shipping") or ad_data.get("delivery")
        if isinstance(shipping_info, dict):
            for key in ("price", "amount", "value"):
                if key in shipping_info and shipping_info[key] not in (None, ""):
                    value = shipping_info[key]
                    if isinstance(value, (int, float)):
                        return float(value)
                    if isinstance(value, str):
                        parsed = self._scraping_utils.extract_price(value)
                        if parsed is not None:
                            return parsed

        return None

    @staticmethod
    def _extract_listing_id(ad: Any, ad_data: Dict[str, Any]) -> Optional[str]:
        for key in ("list_id", "id", "ad_id", "document_id", "slug"):
            value = ad_data.get(key)
            if value:
                return str(value)
            if hasattr(ad, key):
                value = getattr(ad, key)
                if value:
                    return str(value)

        url = ad_data.get("url")
        if isinstance(url, str) and url:
            return url.rsplit("/", 1)[-1]

        return None


async def fetch_leboncoin_api_listings(
    keyword: str,
    limit: int = 50,
    **search_kwargs: Any,
) -> List[Listing]:
    connector = LeBonCoinAPIConnector()
    url = search_kwargs.pop("url", None)
    locations = search_kwargs.pop("locations", None)
    sort = search_kwargs.pop("sort", lbc.Sort.NEWEST)
    ad_type = search_kwargs.pop("ad_type", lbc.AdType.OFFER)
    owner_type = search_kwargs.pop("owner_type", lbc.OwnerType.ALL)

    return await connector.search_items(
        keyword=keyword,
        url=url,
        limit=limit,
        locations=locations,
        sort=sort,
        ad_type=ad_type,
        owner_type=owner_type,
        extra_filters=search_kwargs,
    )


async def fetch_leboncoin_api_sold(keyword: str, limit: int = 50) -> List[Listing]:
    logger.warning("LeBonCoin API does not expose sold listings; returning active listings as proxy.")
    return await fetch_leboncoin_api_listings(keyword, limit)


def parse_leboncoin_api_ads(ads: Iterable[Any]) -> List[Listing]:
    connector = LeBonCoinAPIConnector()
    return [listing for ad in ads if (listing := connector._map_ad_to_listing(ad))]

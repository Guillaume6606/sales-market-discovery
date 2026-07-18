"""eBay connector backed by the Browse API (OAuth2 client-credentials).

The legacy Finding and Shopping APIs were decommissioned in Feb 2025; this
module talks to the Buy Browse API instead:

- ``fetch_ebay_listings`` — active listings via ``item_summary/search``.
- ``fetch_ebay_sold`` — TRUE sold data needs the approval-gated Marketplace
  Insights API (application pending); until granted this returns empty and
  PMN falls back to active-listing statistics downstream.
- ``fetch_detail`` — single-item detail via ``item/{item_id}``.
"""

import base64
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
from loguru import logger

from libs.common.condition import normalize_condition
from libs.common.models import Listing
from libs.common.settings import settings

if TYPE_CHECKING:
    from libs.common.models import ListingDetail

EBAY_MARKETPLACE_ID = "EBAY_FR"
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
TOKEN_EXPIRY_MARGIN_S = 60

# Module-level app token cache (client-credentials tokens live ~2h)
_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _is_sandbox() -> bool:
    return bool(settings.ebay_app_id and "-SBX-" in settings.ebay_app_id)


def _api_host() -> str:
    return "https://api.sandbox.ebay.com" if _is_sandbox() else "https://api.ebay.com"


def _credentials_ready() -> bool:
    if not settings.ebay_app_id or not settings.ebay_cert_id:
        logger.warning("EBAY_APP_ID / EBAY_CERT_ID not set; returning empty result")
        return False
    return True


def _token_request_args() -> tuple[str, dict[str, str], dict[str, str]]:
    raw = f"{settings.ebay_app_id}:{settings.ebay_cert_id}".encode()
    headers = {
        "Authorization": f"Basic {base64.b64encode(raw).decode()}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials", "scope": OAUTH_SCOPE}
    return f"{_api_host()}/identity/v1/oauth2/token", headers, data


def _cache_token(payload: dict[str, Any]) -> str:
    token = str(payload["access_token"])
    expires_in = float(payload.get("expires_in", 7200))
    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + expires_in - TOKEN_EXPIRY_MARGIN_S
    return token


def _cached_token() -> str | None:
    token = _token_cache.get("token")
    if token and time.time() < float(_token_cache["expires_at"]):
        return str(token)
    return None


async def _get_app_token() -> str | None:
    """Fetch (or reuse) an OAuth2 application access token."""
    cached = _cached_token()
    if cached:
        return cached
    url, headers, data = _token_request_args()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, headers=headers, data=data)
            r.raise_for_status()
            return _cache_token(r.json())
    except httpx.HTTPStatusError as e:
        logger.error(f"eBay OAuth token error: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        logger.error(f"eBay OAuth token request failed: {e}")
    return None


def _get_app_token_sync() -> str | None:
    """Sync variant for the sync ``fetch_detail`` path; shares the same cache."""
    cached = _cached_token()
    if cached:
        return cached
    url, headers, data = _token_request_args()
    try:
        r = httpx.post(url, headers=headers, data=data, timeout=15)
        r.raise_for_status()
        return _cache_token(r.json())
    except httpx.HTTPStatusError as e:
        logger.error(f"eBay OAuth token error: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        logger.error(f"eBay OAuth token request failed: {e}")
    return None


async def fetch_ebay_sold(keyword: str, limit: int = 50) -> list[Listing]:
    """Sold listings require the Marketplace Insights API (approval pending).

    Returning empty (instead of relabeling active listings as sold) keeps PMN
    honest — downstream computation falls back to active-listing statistics.
    """
    logger.warning(
        "eBay sold data unavailable (Marketplace Insights not granted); returning empty for '{}'",
        keyword,
    )
    return []


async def fetch_ebay_listings(keyword: str, limit: int = 50) -> list[Listing]:
    """Fetch active listings from the Browse API ``item_summary/search``."""
    if not _credentials_ready():
        return []
    token = await _get_app_token()
    if not token:
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
    }
    params = {
        "q": keyword,
        "limit": str(min(limit, 200)),  # Browse API max page size
        "filter": "priceCurrency:EUR",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{_api_host()}/buy/browse/v1/item_summary/search",
                headers=headers,
                params=params,
            )
            r.raise_for_status()
            return parse_ebay_browse_response(r.json(), is_sold=False)
    except httpx.HTTPStatusError as e:
        logger.error(
            f"eBay Browse API HTTP error for '{keyword}': "
            f"{e.response.status_code} - {e.response.text}"
        )
    except httpx.RequestError as e:
        logger.error(f"eBay Browse API request error for '{keyword}': {e}")
    except Exception as e:
        logger.error(f"Unexpected error fetching eBay listings for '{keyword}': {e}", exc_info=True)
    return []


def _extract_brand_from_title(title: str) -> str | None:
    """Attempt to extract brand from product title using common patterns"""
    if not title:
        return None

    # Common brand patterns
    brands = [
        "Sony",
        "Apple",
        "Samsung",
        "Nike",
        "Adidas",
        "Canon",
        "Nikon",
        "Dell",
        "HP",
        "Lenovo",
        "Asus",
        "Microsoft",
        "Nintendo",
        "PlayStation",
    ]

    title_lower = title.lower()
    for brand in brands:
        if brand.lower() in title_lower:
            return brand

    return None


def _shipping_cost_from_options(item: dict[str, Any]) -> float | None:
    for option in item.get("shippingOptions") or []:
        cost = (option or {}).get("shippingCost") or {}
        value = cost.get("value")
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                continue
    return None


def _location_from_item(item: dict[str, Any]) -> str | None:
    loc = item.get("itemLocation") or {}
    parts = [loc.get("city"), loc.get("postalCode"), loc.get("country")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def parse_ebay_browse_response(response_data: dict, is_sold: bool = False) -> list[Listing]:
    """Parse a Browse API ``item_summary/search`` response into ``Listing`` objects."""
    if not response_data:
        return []

    if "errors" in response_data:
        logger.error(f"eBay Browse API error: {response_data['errors']}")
        return []

    items = response_data.get("itemSummaries") or []
    if not items:
        logger.info("eBay search returned 0 results")
        return []

    parsed_items = []
    for item in items:
        try:
            listing_id = item.get("itemId") or ""
            if not listing_id:
                continue

            title = item.get("title") or ""

            price_data = item.get("price") or {}
            price_value = price_data.get("value")
            if price_value is None:
                continue
            try:
                price = float(price_value)
            except (ValueError, TypeError):
                logger.warning(f"Invalid price value for item {listing_id}: {price_value}")
                continue
            if price <= 0:
                logger.debug(f"Skipping item {listing_id} with invalid price: {price}")
                continue

            currency = price_data.get("currency") or "EUR"

            seller_rating = None
            feedback_score = (item.get("seller") or {}).get("feedbackScore")
            if feedback_score is not None:
                try:
                    seller_rating = float(feedback_score)
                except (ValueError, TypeError):
                    pass

            condition = item.get("condition") or "Unknown"

            legacy_id = item.get("legacyItemId")
            url = item.get("itemWebUrl") or (
                f"https://www.ebay.fr/itm/{legacy_id}" if legacy_id else None
            )

            listing = Listing(
                source="ebay",
                listing_id=listing_id,
                title=title,
                price=price,
                currency=currency,
                condition_raw=condition,
                condition_norm=normalize_condition(condition),
                location=_location_from_item(item),
                seller_rating=seller_rating,
                shipping_cost=_shipping_cost_from_options(item),
                observed_at=datetime.now(UTC),
                is_sold=is_sold,
                url=url,
                brand=_extract_brand_from_title(title),
                size=None,  # Not available in item summaries
                color=None,  # Not available in item summaries
            )
            parsed_items.append(listing)

        except Exception as e:
            logger.warning(f"Error parsing eBay item: {e}", exc_info=True)
            continue

    logger.info(f"Parsed {len(parsed_items)} eBay items from {len(items)} raw items")
    return parsed_items


def fetch_detail(listing_id: str, obs_id: int) -> "ListingDetail | None":
    """Fetch detailed data for a single eBay listing via Browse API ``getItem``.

    Args:
        listing_id: Browse RESTful item id (``v1|123|0``) or a legacy numeric id.
        obs_id: The observation ID to associate with the detail record.

    Returns:
        A populated ``ListingDetail`` or ``None`` if the fetch fails.
    """
    from libs.common.models import ListingDetail

    if not _credentials_ready():
        return None
    token = _get_app_token_sync()
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
    }
    if listing_id.isdigit():
        url = f"{_api_host()}/buy/browse/v1/item/get_item_by_legacy_id"
        params: dict[str, str] = {"legacy_item_id": listing_id}
    else:
        url = f"{_api_host()}/buy/browse/v1/item/{quote(listing_id, safe='')}"
        params = {}

    try:
        resp = httpx.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        item = resp.json()
    except Exception:
        logger.exception("eBay Browse getItem failed for {}", listing_id)
        return None

    if not item or "itemId" not in item:
        return None

    pictures: list[str] = []
    primary = (item.get("image") or {}).get("imageUrl")
    if primary:
        pictures.append(primary)
    for extra in item.get("additionalImages") or []:
        extra_url = (extra or {}).get("imageUrl")
        if extra_url:
            pictures.append(extra_url)

    description = item.get("description") or None

    original_posted_at = None
    creation_date = item.get("itemCreationDate")
    if creation_date:
        try:
            original_posted_at = datetime.fromisoformat(creation_date.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    feedback_score = (item.get("seller") or {}).get("feedbackScore")
    seller_transaction_count = int(feedback_score) if feedback_score is not None else None

    watch_count = item.get("watchCount")
    favorite_count = int(watch_count) if watch_count is not None else None

    buying_options = item.get("buyingOptions") or []
    negotiation_enabled = "BEST_OFFER" in buying_options

    local_pickup_only = bool(item.get("pickupOptions")) and not item.get("shippingOptions")

    return ListingDetail(
        obs_id=obs_id,
        description=description,
        photo_urls=pictures,
        local_pickup_only=local_pickup_only,
        negotiation_enabled=negotiation_enabled,
        original_posted_at=original_posted_at,
        seller_account_age_days=None,  # Not exposed by the Browse API
        seller_transaction_count=seller_transaction_count,
        view_count=None,  # Not exposed by the Browse API
        favorite_count=favorite_count,
    )

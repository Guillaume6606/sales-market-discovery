from typing import Any
import httpx
from loguru import logger
from libs.common.settings import settings
from libs.common.models import Listing
from datetime import datetime, timezone
import json

EBAY_FINDING_API = "https://svcs.ebay.com/services/search/FindingService/v1"

async def fetch_ebay_sold(keyword: str, limit: int = 50) -> list[Listing]:
    """Fetch sold items from eBay"""
    if not settings.ebay_app_id:
        logger.warning("EBAY_APP_ID not set; returning empty result")
        return []

    headers = {"X-EBAY-SOA-SECURITY-APPNAME": settings.ebay_app_id}
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "RESPONSE-DATA-FORMAT": "JSON",
        "keywords": keyword,
        "paginationInput.entriesPerPage": str(limit),
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(EBAY_FINDING_API, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            return data.get("findCompletedItemsResponse", [{}])
    except Exception as e:
        logger.error(f"Error fetching eBay sold items: {e}")
        return [{}]

async def fetch_ebay_listings(keyword: str, limit: int = 50) -> list[Listing]:
    """Fetch current active listings from eBay"""
    if not settings.ebay_app_id:
        logger.warning("EBAY_APP_ID not set; returning empty result")
        return []

    headers = {"X-EBAY-SOA-SECURITY-APPNAME": settings.ebay_app_id}
    params = {
        "OPERATION-NAME": "findItemsByKeywords",
        "SERVICE-VERSION": "1.13.0",
        "RESPONSE-DATA-FORMAT": "JSON",
        "keywords": keyword,
        "paginationInput.entriesPerPage": str(limit),
        "itemFilter(0).name": "HideDuplicateItems",
        "itemFilter(0).value": "true",
        "itemFilter(1).name": "Condition",
        "itemFilter(1).value": "Used",
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(EBAY_FINDING_API, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            return data.get("findItemsByKeywordsResponse", [{}])
    except Exception as e:
        logger.error(f"Error fetching eBay listings: {e}")
        return [{}]

def normalize_condition(condition_raw: str) -> str | None:
    """Normalize eBay condition to standard categories"""
    if not condition_raw:
        return None

    condition_lower = condition_raw.lower()

    # eBay condition mappings
    if any(word in condition_lower for word in ["new", "brand new", "nib"]):
        return "new"
    elif any(word in condition_lower for word in ["like new", "excellent", "mint"]):
        return "like_new"
    elif any(word in condition_lower for word in ["very good", "good"]):
        return "good"
    elif any(word in condition_lower for word in ["acceptable", "fair", "poor"]):
        return "fair"

    return None

def parse_ebay_response(response_data: dict, is_sold: bool = False) -> list[Listing]:
    """Parse eBay API response into standardized format"""
    if not response_data or "searchResult" not in response_data:
        return []

    items = response_data["searchResult"][0].get("item", [])
    if not isinstance(items, list):
        items = [items]

    parsed_items = []
    for item in items:
        try:
            # Extract basic item info
            listing_id = item.get("itemId", [""])[0]
            title = item.get("title", [""])[0]
            price_info = item.get("sellingStatus", [{}])[0].get("currentPrice", [{}])[0]
            price = float(price_info.get("value", ["0"])[0]) if price_info.get("value") else 0.0
            currency = price_info.get("currencyId", ["USD"])[0]

            # Extract seller info
            seller_info = item.get("sellerInfo", [{}])[0]
            seller_rating = float(seller_info.get("feedbackScore", ["0"])[0]) if seller_info.get("feedbackScore") else None

            # Extract shipping info
            shipping_info = item.get("shippingInfo", [{}])[0]
            shipping_cost = 0.0
            if shipping_info.get("shippingServiceCost"):
                shipping_cost = float(shipping_info["shippingServiceCost"][0].get("value", ["0"])[0])

            # Extract location
            location = item.get("location", [""])[0]

            # Extract condition
            condition = item.get("condition", [{}])[0].get("conditionDisplayName", ["Unknown"])[0]

            # Create standardized Listing object
            listing = Listing(
                source="ebay",
                listing_id=listing_id,
                title=title,
                price=price,
                currency=currency,
                condition_raw=condition,
                condition_norm=normalize_condition(condition),
                location=location,
                seller_rating=seller_rating,
                shipping_cost=shipping_cost,
                observed_at=datetime.now(timezone.utc),
                is_sold=is_sold,
                url=f"https://www.ebay.com/itm/{listing_id}" if listing_id else None
            )

            parsed_items.append(listing)

        except Exception as e:
            logger.warning(f"Error parsing eBay item: {e}")
            continue

    return parsed_items

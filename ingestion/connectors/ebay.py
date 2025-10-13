from typing import Any, List
import httpx
from loguru import logger
from libs.common.settings import settings
from libs.common.models import Listing
from datetime import datetime, timezone
import json

# eBay API endpoints
EBAY_FINDING_API_PRODUCTION = "https://svcs.ebay.com/services/search/FindingService/v1"
EBAY_FINDING_API_SANDBOX = "https://svcs.sandbox.ebay.com/services/search/FindingService/v1"

def _get_ebay_api_url() -> str:
    """Determine which eBay API endpoint to use based on the App ID"""
    if settings.ebay_app_id and "-SBX-" in settings.ebay_app_id:
        logger.info("Using eBay Sandbox API (detected sandbox App ID)")
        return EBAY_FINDING_API_SANDBOX
    else:
        logger.debug("Using eBay Production API")
        return EBAY_FINDING_API_PRODUCTION

async def fetch_ebay_sold(keyword: str, limit: int = 50) -> List[Listing]:
    """Fetch sold items from eBay Finding API and return parsed Listing objects"""
    if not settings.ebay_app_id:
        logger.warning("EBAY_APP_ID not set; returning empty result")
        return []

    api_url = _get_ebay_api_url()
    headers = {"X-EBAY-SOA-SECURITY-APPNAME": settings.ebay_app_id}
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "RESPONSE-DATA-FORMAT": "JSON",
        "keywords": keyword,
        "paginationInput.entriesPerPage": str(min(limit, 100)),  # eBay max is 100
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        # Add currency filter to get consistent pricing
        "itemFilter(1).name": "Currency",
        "itemFilter(1).value": "EUR",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(api_url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            
            # eBay wraps response in an array - extract first element
            if "findCompletedItemsResponse" in data:
                response_data = data["findCompletedItemsResponse"]
                if isinstance(response_data, list) and len(response_data) > 0:
                    return parse_ebay_response(response_data[0], is_sold=True)
            
            logger.warning(f"Unexpected eBay response structure for keyword '{keyword}'")
            return []
            
    except httpx.HTTPStatusError as e:
        logger.error(f"eBay API HTTP error for keyword '{keyword}': {e.response.status_code} - {e.response.text}")
        return []
    except httpx.RequestError as e:
        logger.error(f"eBay API request error for keyword '{keyword}': {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching eBay sold items for '{keyword}': {e}", exc_info=True)
        return []

async def fetch_ebay_listings(keyword: str, limit: int = 50) -> List[Listing]:
    """Fetch current active listings from eBay Finding API and return parsed Listing objects"""
    if not settings.ebay_app_id:
        logger.warning("EBAY_APP_ID not set; returning empty result")
        return []

    api_url = _get_ebay_api_url()
    headers = {"X-EBAY-SOA-SECURITY-APPNAME": settings.ebay_app_id}
    params = {
        "OPERATION-NAME": "findItemsByKeywords",
        "SERVICE-VERSION": "1.13.0",
        "RESPONSE-DATA-FORMAT": "JSON",
        "keywords": keyword,
        "paginationInput.entriesPerPage": str(min(limit, 100)),  # eBay max is 100
        "itemFilter(0).name": "HideDuplicateItems",
        "itemFilter(0).value": "true",
        # Add currency filter to get consistent pricing
        "itemFilter(1).name": "Currency",
        "itemFilter(1).value": "EUR",
        # Remove Used-only filter to get all conditions
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(api_url, headers=headers, params=params)
            r.raise_for_status()
            data = r.json()
            
            # eBay wraps response in an array - extract first element
            if "findItemsByKeywordsResponse" in data:
                response_data = data["findItemsByKeywordsResponse"]
                if isinstance(response_data, list) and len(response_data) > 0:
                    return parse_ebay_response(response_data[0], is_sold=False)
            
            logger.warning(f"Unexpected eBay response structure for keyword '{keyword}'")
            return []
            
    except httpx.HTTPStatusError as e:
        logger.error(f"eBay API HTTP error for keyword '{keyword}': {e.response.status_code} - {e.response.text}")
        return []
    except httpx.RequestError as e:
        logger.error(f"eBay API request error for keyword '{keyword}': {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching eBay listings for '{keyword}': {e}", exc_info=True)
        return []

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

def _safe_extract(data: Any, default: Any = None) -> Any:
    """Safely extract value from eBay's nested array structure"""
    if isinstance(data, list) and len(data) > 0:
        return data[0]
    return default


def _extract_brand_from_title(title: str) -> str | None:
    """Attempt to extract brand from product title using common patterns"""
    if not title:
        return None
    
    # Common brand patterns
    brands = ["Sony", "Apple", "Samsung", "Nike", "Adidas", "Canon", "Nikon", 
              "Dell", "HP", "Lenovo", "Asus", "Microsoft", "Nintendo", "PlayStation"]
    
    title_lower = title.lower()
    for brand in brands:
        if brand.lower() in title_lower:
            return brand
    
    return None


def parse_ebay_response(response_data: dict, is_sold: bool = False) -> List[Listing]:
    """
    Parse eBay Finding API response into standardized Listing objects.
    
    eBay API structure:
    - Response is wrapped in array: data["findCompletedItemsResponse"][0]
    - Most fields are wrapped in arrays: item.get("itemId")[0]
    - searchResult contains the items array
    """
    if not response_data:
        return []
    
    # Check for API errors
    if "errorMessage" in response_data:
        error = response_data["errorMessage"]
        logger.error(f"eBay API error: {error}")
        return []
    
    # Extract search results
    search_result = _safe_extract(response_data.get("searchResult", []))
    if not search_result:
        logger.warning("No searchResult in eBay response")
        return []
    
    # Check if any items found
    count = _safe_extract(search_result.get("@count", ["0"]))
    if count == "0":
        logger.info("eBay search returned 0 results")
        return []
    
    # Extract items array
    items = search_result.get("item", [])
    if not isinstance(items, list):
        items = [items] if items else []
    
    if not items:
        logger.info("No items in eBay search results")
        return []

    parsed_items = []
    for item in items:
        try:
            # Extract basic item info with safe array unwrapping
            listing_id = _safe_extract(item.get("itemId"), "")
            if not listing_id:
                continue  # Skip items without ID
            
            title = _safe_extract(item.get("title"), "")
            
            # Extract price info
            selling_status = _safe_extract(item.get("sellingStatus"))
            if not selling_status:
                continue  # Skip items without price
            
            current_price = _safe_extract(selling_status.get("currentPrice"))
            if not current_price:
                continue
            
            price_value = _safe_extract(current_price.get("__value__"))
            if not price_value:
                continue
            
            try:
                price = float(price_value)
            except (ValueError, TypeError):
                logger.warning(f"Invalid price value for item {listing_id}: {price_value}")
                continue
            
            # Skip items with zero or negative price
            if price <= 0:
                logger.debug(f"Skipping item {listing_id} with invalid price: {price}")
                continue
            
            currency = _safe_extract(current_price.get("@currencyId"), "EUR")

            # Extract seller info
            seller_info = _safe_extract(item.get("sellerInfo"))
            seller_rating = None
            if seller_info:
                feedback_score = _safe_extract(seller_info.get("feedbackScore"))
                if feedback_score:
                    try:
                        seller_rating = float(feedback_score)
                    except (ValueError, TypeError):
                        pass

            # Extract shipping info
            shipping_info = _safe_extract(item.get("shippingInfo"))
            shipping_cost = None
            if shipping_info:
                shipping_service_cost = _safe_extract(shipping_info.get("shippingServiceCost"))
                if shipping_service_cost:
                    shipping_value = _safe_extract(shipping_service_cost.get("__value__"))
                    if shipping_value:
                        try:
                            shipping_cost = float(shipping_value)
                        except (ValueError, TypeError):
                            pass

            # Extract location
            location = _safe_extract(item.get("location"), "")

            # Extract condition
            condition_data = _safe_extract(item.get("condition"))
            condition = "Unknown"
            if condition_data:
                condition = _safe_extract(condition_data.get("conditionDisplayName"), "Unknown")
            
            # Extract brand from title (eBay Finding API doesn't provide brand field)
            brand = _extract_brand_from_title(title)

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
                url=f"https://www.ebay.com/itm/{listing_id}",
                brand=brand,
                size=None,  # Not available in Finding API
                color=None,  # Not available in Finding API
            )

            parsed_items.append(listing)

        except Exception as e:
            logger.warning(f"Error parsing eBay item: {e}", exc_info=True)
            continue

    logger.info(f"Parsed {len(parsed_items)} eBay items from {len(items)} raw items")
    return parsed_items

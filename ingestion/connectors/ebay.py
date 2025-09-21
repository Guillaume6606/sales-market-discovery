from typing import Any
import httpx
from loguru import logger
from libs.common.settings import settings

EBAY_FINDING_API = "https://svcs.ebay.com/services/search/FindingService/v1"

async def fetch_ebay_sold(keyword: str, limit: int = 50) -> list[dict[str, Any]]:
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
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(EBAY_FINDING_API, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()
        return data.get("findCompletedItemsResponse", [])

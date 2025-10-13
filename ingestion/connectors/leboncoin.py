"""
LeBonCoin connector using advanced web scraping
"""
import asyncio
import json
import re
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from urllib.parse import urlencode, quote
from bs4 import BeautifulSoup
from loguru import logger

from libs.common.scraping import ScrapingSession, ScrapingUtils, scraping_config
from libs.common.models import Listing

class LeBonCoinConnector:
    """LeBonCoin scraping connector"""

    BASE_URL = "https://www.leboncoin.fr"

    def __init__(self):
        self.scraping_utils = ScrapingUtils()

    def normalize_condition_leboncoin(self, condition_raw: str) -> str | None:
        """Normalize LeBonCoin condition to standard categories"""
        if not condition_raw:
            return None

        condition_lower = condition_raw.lower()

        # LeBonCoin condition mappings (French)
        if any(word in condition_lower for word in ["neuf", "new", "nouveau"]):
            return "new"
        elif any(word in condition_lower for word in ["Très bon état", "comme neuf", "like new", "excellent"]):
            return "like_new"
        elif any(word in condition_lower for word in ["bon état", "good", "bien"]):
            return "good"
        elif any(word in condition_lower for word in ["satisfaisant", "fair", "acceptable"]):
            return "fair"

        return None

    async def search_items(self, keyword: str, category: str = "", limit: int = 50) -> List[Listing]:
        """
        Search for items on LeBonCoin

        Args:
            keyword: Search keyword
            category: Category filter (optional)
            limit: Maximum number of results

        Returns:
            List of parsed item data
        """
        logger.info(f"Searching LeBonCoin for: {keyword}")

        items = []

        # LeBonCoin search URL construction
        search_params = {
            "text": keyword,
            "category": category,
            "limit": min(limit, 50),  # LeBonCoin limits to 50 per page
        }

        # Remove empty category
        if not category:
            search_params.pop("category")

        query_string = urlencode(search_params)
        search_url = f"{self.BASE_URL}/recherche?{query_string}"

        try:
            async with ScrapingSession(scraping_config) as session:
                # Get search results page (with fallback to HTTP if Playwright fails)
                html_content = await session.get_html_with_fallback(search_url)
                logger.info(f"HTML Content: {html_content}")
                # Parse items from the page
                page_items = self._parse_search_results(html_content)
                logger.info(f"Page Items: {page_items}")
                # Add metadata and convert to Listing objects
                for item_dict in page_items:
                    listing = Listing(
                        source="leboncoin",
                        listing_id=item_dict["listing_id"],
                        title=item_dict["title"],
                        price=item_dict.get("price"),
                        currency=item_dict.get("currency", "EUR"),
                        condition_raw=item_dict.get("condition"),
                        condition_norm=self.normalize_condition_leboncoin(item_dict.get("condition", "")),
                        location=item_dict.get("location"),
                        seller_rating=1.0 if item_dict.get("is_pro") else 0.0,
                        shipping_cost=item_dict.get("shipping_cost"),
                        observed_at=datetime.now(timezone.utc),
                        is_sold=False,
                        url=item_dict.get("item_url")
                    )
                    items.append(listing)

                items = items[:limit]

                logger.info(f"Found {len(items)} items on LeBonCoin for keyword: {keyword}")

        except Exception as e:
            logger.error(f"Error searching LeBonCoin for {keyword}: {e}")

        return items

    async def get_item_details(self, item_url: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific item

        Args:
            item_url: Full URL to the item page

        Returns:
            Detailed item information or None if failed
        """
        try:
            async with ScrapingSession(scraping_config) as session:
                html_content = await session.get_html_with_fallback(item_url)

                # Parse detailed item information
                item_details = self._parse_item_details(html_content, item_url)

                if item_details:
                    item_details.update({
                        "source": "leboncoin",
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "item_url": item_url
                    })

                return item_details

        except Exception as e:
            logger.error(f"Error getting item details from {item_url}: {e}")
            return None

    def _parse_search_results(self, html_content: str) -> List[Dict[str, Any]]:
        """Parse search results HTML"""
        soup = BeautifulSoup(html_content, 'html.parser')
        # 1) Prefer structured data exposed by the Next.js payload (contains full ad objects)
        items_from_json = self._parse_next_data(soup)
        if items_from_json:
            logger.debug(f"Parsed {len(items_from_json)} items from __NEXT_DATA__ payload")
            return items_from_json

        # 2) Fallback to scraping rendered cards if structured data is missing
        items: List[Dict[str, Any]] = []
        item_selectors = [
            '[data-testid="ad-card"]',
            'a[data-qa-id="aditem_container"]',
            '.ad',
            '.item',
            '[class*="ad-"]',
        ]

        item_elements = []
        for selector in item_selectors:
            item_elements = soup.select(selector)
            if item_elements:
                logger.debug(f"Found {len(item_elements)} items using selector: {selector}")
                break

        if not item_elements:
            logger.warning("No item elements found in search results (structured payload unavailable)")
            return items

        for element in item_elements:
            try:
                item = self._parse_item_element(element)
                if item:
                    items.append(item)
            except Exception as e:
                logger.warning(f"Error parsing item element: {e}")

        return items

    def _parse_next_data(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract listings from the Next.js __NEXT_DATA__ payload."""
        script_tag = soup.find("script", id="__NEXT_DATA__", type="application/json")
        if not script_tag or not script_tag.string:
            return []

        try:
            payload = json.loads(script_tag.string)
        except json.JSONDecodeError as exc:
            logger.warning(f"Failed to decode __NEXT_DATA__ payload: {exc}")
            return []

        ads = self._extract_ads_from_json(payload)
        if not ads:
            return []

        items: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for ad in ads:
            item = self._map_ad_to_item(ad)
            if not item:
                continue
            listing_id = item["listing_id"]
            if listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)
            items.append(item)

        return items

    def _extract_ads_from_json(self, payload: Any) -> List[Dict[str, Any]]:
        """Walk the JSON payload and collect dictionaries that look like ads."""
        stack = [payload]
        ads: List[Dict[str, Any]] = []

        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                if {"list_id", "subject"}.issubset(current.keys()):
                    ads.append(current)
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)

        return ads

    def _map_ad_to_item(self, ad: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Convert a raw ad dictionary to the internal item format."""
        listing_id = ad.get("list_id") or ad.get("ad_id") or ad.get("id")
        if listing_id is None:
            return None

        listing_id = str(listing_id)

        title = ad.get("subject") or ad.get("title") or ""
        description = ad.get("body") or ad.get("description") or ""

        price_info = ad.get("price")
        price: Optional[float] = None
        currency = "EUR"

        if isinstance(price_info, dict):
            potential_values = [
                price_info.get("value"),
                price_info.get("amount"),
                price_info.get("price"),
            ]
            for value in potential_values:
                if isinstance(value, (int, float)):
                    price = float(value)
                    break
                if isinstance(value, str):
                    extracted = self.scraping_utils.extract_price(value)
                    if extracted is not None:
                        price = extracted
                        break

            currency = price_info.get("currency") or currency
        elif isinstance(price_info, (int, float)):
            price = float(price_info)
        elif isinstance(price_info, str):
            price = self.scraping_utils.extract_price(price_info)

        location_info = ad.get("location") or {}
        if isinstance(location_info, dict):
            location_parts = [
                location_info.get("city_label") or location_info.get("city"),
                location_info.get("zipcode") or location_info.get("zip_code"),
            ]
            location = ", ".join(part for part in location_parts if part)
            location = location or None
        else:
            location = None

        images_info = ad.get("images") or []
        image_url = ""
        if isinstance(images_info, dict):
            image_candidates = [
                images_info.get("url"),
                images_info.get("large_url"),
                images_info.get("small_url"),
            ]
            image_url = next((u for u in image_candidates if u), "")
        elif isinstance(images_info, list) and images_info:
            first_image = images_info[0]
            if isinstance(first_image, dict):
                image_url = first_image.get("url") or first_image.get("href", "")
            elif isinstance(first_image, str):
                image_url = first_image

        item_url = ad.get("url") or ad.get("permalink") or ad.get("short_url")
        if not item_url:
            slug = ad.get("slug")
            if slug:
                item_url = f"{self.BASE_URL}/ad/{slug}/{listing_id}"

        if item_url and not item_url.startswith("http"):
            item_url = f"{self.BASE_URL}{item_url}"

        owner_info = ad.get("owner") or {}
        owner_type = (owner_info.get("type") or ad.get("owner_type") or "").lower()
        is_pro = owner_type in {"pro", "professional"}

        condition = None
        attributes = ad.get("attributes") or []
        if isinstance(attributes, list):
            for attribute in attributes:
                if not isinstance(attribute, dict):
                    continue
                key = attribute.get("key") or attribute.get("name")
                if key in {"condition", "etat"}:
                    condition = attribute.get("value_label") or attribute.get("value")
                    break

        category = ad.get("category_name") or ad.get("category_label") or ad.get("category")

        date_listed = ad.get("first_publication_date") or ad.get("index_date") or ad.get("publication_date")

        shipping_cost = None
        shipping_info = ad.get("shipping") or price_info or {}
        if isinstance(shipping_info, dict):
            shipping_value = shipping_info.get("shipping_price") or shipping_info.get("cost")
            if isinstance(shipping_value, (int, float)):
                shipping_cost = float(shipping_value)
            elif isinstance(shipping_value, str):
                extracted_shipping = self.scraping_utils.extract_price(shipping_value)
                if extracted_shipping is not None:
                    shipping_cost = extracted_shipping

        return {
            "listing_id": listing_id,
            "title": self.scraping_utils.clean_text(title),
            "price": price,
            "currency": currency or "EUR",
            "location": self.scraping_utils.clean_text(location) if location else None,
            "description": self.scraping_utils.clean_text(description),
            "image_url": image_url,
            "item_url": item_url,
            "is_pro": is_pro,
            "date_listed": date_listed,
            "category": category,
            "condition": condition,
            "shipping_cost": shipping_cost,
        }

    def _parse_item_element(self, element) -> Optional[Dict[str, Any]]:
        """Parse individual item element"""
        try:
            # Extract item URL
            link_element = element if element.name == 'a' and element.get('href') else element.select_one('a[href]')
            if not link_element:
                return None

            item_url = link_element['href']
            if not item_url.startswith('http'):
                item_url = f"{self.BASE_URL}{item_url}"

            # Extract title
            title_element = (
                element.select_one('[data-testid="ad-title"]')
                or element.select_one('h3')
                or element.select_one('h2')
                or element.select_one('.title')
            )
            title = self.scraping_utils.clean_text(title_element.get_text()) if title_element else ""

            # Extract price
            price_element = (
                element.select_one('[data-testid="price"]')
                or element.select_one('span[class*="Price"]')
                or element.select_one('span[class*="price"]')
            )
            price_text = price_element.get_text() if price_element else ""
            price = self.scraping_utils.extract_price(price_text)

            # Extract location
            location_element = (
                element.select_one('[data-testid="location"]')
                or element.select_one('span[class*="Location"]')
                or element.select_one('span[class*="location"]')
                or element.select_one('p[class*="location"]')
            )
            location_text = location_element.get_text() if location_element else ""
            location = self.scraping_utils.extract_location(location_text)

            # Extract date
            date_element = (
                element.select_one('time')
                or element.select_one('[data-testid="date"]')
                or element.select_one('span[class*="Date"]')
                or element.select_one('span[class*="date"]')
            )
            date_text = date_element.get_text() if date_element else ""
            date_obj = self.scraping_utils.extract_date(date_text)

            # Extract image URL
            image_element = element.select_one('img[src]')
            image_url = image_element['src'] if image_element else ""

            # Extract description/preview text
            desc_element = (
                element.select_one('[data-testid="description"]')
                or element.select_one('p[class*="description"]')
                or element.select_one('span[class*="description"]')
                or element.select_one('p[class*="text"]')
            )
            description = self.scraping_utils.clean_text(desc_element.get_text()) if desc_element else ""

            # Determine if it's a professional seller (pro)
            is_pro = False
            pro_indicator = element.select_one('[data-testid="pro-label"], [class*="Pro"], [class*="pro"]')
            if pro_indicator:
                is_pro = True

            return {
                "listing_id": self._extract_listing_id(item_url),
                "title": title,
                "price": price,
                "currency": "EUR",  # LeBonCoin is French/Euro
                "location": location,
                "description": description,
                "image_url": image_url,
                "item_url": item_url,
                "is_pro": is_pro,
                "date_listed": date_obj.isoformat() if date_obj else None,
                "category": self._extract_category_from_element(element),
                "condition": self._extract_condition_from_element(element),
            }

        except Exception as e:
            logger.error(f"Error parsing item element: {e}")
            return None

    def _parse_item_details(self, html_content: str, item_url: str) -> Optional[Dict[str, Any]]:
        """Parse detailed item page"""
        soup = BeautifulSoup(html_content, 'html.parser')

        try:
            # Try to find JSON-LD structured data first (more reliable)
            json_ld = soup.find('script', type='application/ld+json')
            if json_ld:
                try:
                    structured_data = json.loads(json_ld.string)
                    return self._parse_structured_data(structured_data, item_url)
                except json.JSONDecodeError:
                    pass

            # Fallback to HTML parsing
            return self._parse_item_html(soup, item_url)

        except Exception as e:
            logger.error(f"Error parsing item details: {e}")
            return None

    def _parse_structured_data(self, structured_data: Dict, item_url: str) -> Dict[str, Any]:
        """Parse JSON-LD structured data"""
        try:
            # LeBonCoin might use different structured data formats
            if isinstance(structured_data, list):
                structured_data = structured_data[0]

            # Extract basic information
            title = structured_data.get('name', '')
            description = structured_data.get('description', '')

            # Extract price
            offers = structured_data.get('offers', {})
            if isinstance(offers, list):
                offers = offers[0]

            price = offers.get('price')
            if isinstance(price, str):
                price = float(price)

            currency = offers.get('priceCurrency', 'EUR')

            # Extract location
            location = structured_data.get('address', {})
            location_text = location.get('addressLocality', '')

            # Extract images
            images = structured_data.get('image', [])
            if isinstance(images, dict):
                images = [images.get('url', '')]
            elif isinstance(images, str):
                images = [images]

            return {
                "listing_id": self._extract_listing_id(item_url),
                "title": title,
                "description": description,
                "price": price,
                "currency": currency,
                "location": location_text,
                "images": images,
                "category": structured_data.get('category', ''),
            }

        except Exception as e:
            logger.error(f"Error parsing structured data: {e}")
            return {}

    def _parse_item_html(self, soup: BeautifulSoup, item_url: str) -> Dict[str, Any]:
        """Parse item details from HTML"""
        # Extract title
        title_element = soup.find(['h1', '[data-testid="adview-title"]'])
        title = self.scraping_utils.clean_text(title_element.get_text()) if title_element else ""

        # Extract description
        desc_selectors = [
            '[data-testid="adview-description"]',
            '.description',
            '#description',
            '.ad-description'
        ]

        description = ""
        for selector in desc_selectors:
            desc_element = soup.select_one(selector)
            if desc_element:
                description = self.scraping_utils.clean_text(desc_element.get_text())
                break

        # Extract price
        price_selectors = [
            '[data-testid="price"]',
            '.price',
            '.item_price',
            '[class*="price"]'
        ]

        price = None
        for selector in price_selectors:
            price_element = soup.select_one(selector)
            if price_element:
                price_text = price_element.get_text()
                price = self.scraping_utils.extract_price(price_text)
                if price:
                    break

        # Extract location
        location_selectors = [
            '[data-testid="adview-location"]',
            '.location',
            '.item_location',
            '[class*="location"]'
        ]

        location = ""
        for selector in location_selectors:
            location_element = soup.select_one(selector)
            if location_element:
                location = self.scraping_utils.extract_location(location_element.get_text())
                if location:
                    break

        # Extract images
        images = []
        image_selectors = [
            '[data-testid="adview-image"] img',
            '.ad-images img',
            '.item-images img',
            'img[src*="image"]'
        ]

        for selector in image_selectors:
            image_elements = soup.select(selector)
            for img in image_elements[:10]:  # Limit to 10 images
                src = img.get('src') or img.get('data-src')
                if src and src.startswith(('http', '//')):
                    if src.startswith('//'):
                        src = 'https:' + src
                    images.append(src)

        # Extract category
        category_selectors = [
            '[data-testid="breadcrumb"] a:last-child',
            '.breadcrumb a:last-child',
            '.category'
        ]

        category = ""
        for selector in category_selectors:
            cat_element = soup.select_one(selector)
            if cat_element:
                category = self.scraping_utils.clean_text(cat_element.get_text())
                break

        return {
            "listing_id": self._extract_listing_id(item_url),
            "title": title,
            "description": description,
            "price": price,
            "currency": "EUR",
            "location": location,
            "images": images,
            "category": category,
        }

    def _extract_listing_id(self, url: str) -> str:
        """Extract listing ID from URL"""
        # LeBonCoin URLs typically follow pattern: /detail/ID or /ad/ID
        match = re.search(r'/(\d+)', url)
        return match.group(1) if match else url

    def _extract_category_from_element(self, element) -> str:
        """Extract category from item element"""
        # Look for category indicators in the element
        category_element = (
            element.select_one('[data-testid="category"]')
            or element.select_one('span[class*="Category"]')
            or element.select_one('span[class*="category"]')
            or element.select_one('div[class*="category"]')
        )
        if category_element:
            return self.scraping_utils.clean_text(category_element.get_text())
        return "Unknown"

    def _extract_condition_from_element(self, element) -> str:
        """Extract condition from item element"""
        condition_element = (
            element.select_one('[data-testid="condition"]')
            or element.select_one('span[class*="Condition"]')
            or element.select_one('span[class*="condition"]')
            or element.select_one('div[class*="condition"]')
            or element.select_one('span[class*="etat"]')
            or element.select_one('div[class*="etat"]')
        )
        if condition_element:
            return self.scraping_utils.clean_text(condition_element.get_text())
        return "Unknown"

# Convenience functions for backward compatibility with eBay connector
async def fetch_leboncoin_listings(keyword: str, limit: int = 50) -> List[Listing]:
    """Fetch current listings from LeBonCoin"""
    connector = LeBonCoinConnector()
    return await connector.search_items(keyword, limit=limit)

async def fetch_leboncoin_sold(keyword: str, limit: int = 50) -> List[Listing]:
    """
    LeBonCoin doesn't have a direct "sold" API like eBay.
    This is a placeholder that returns recent listings as proxy for sold items.
    """
    logger.warning("LeBonCoin doesn't provide sold items data like eBay. Returning recent listings as proxy.")
    connector = LeBonCoinConnector()
    return await connector.search_items(keyword, limit=limit)

def parse_leboncoin_response(response_data: Dict, is_sold: bool = False) -> List[Listing]:
    """Parse LeBonCoin response (placeholder for compatibility)"""
    # Since we're using scraping, this function is mainly for compatibility
    return response_data if isinstance(response_data, list) else []


"""
Vinted connector using advanced web scraping
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

class VintedConnector:
    """Vinted scraping connector"""

    BASE_URL = "https://www.vinted.fr"

    def __init__(self):
        self.scraping_utils = ScrapingUtils()

    def normalize_condition_vinted(self, condition_raw: str) -> str | None:
        """Normalize Vinted condition to standard categories"""
        if not condition_raw:
            return None

        condition_lower = condition_raw.lower()

        # Vinted condition mappings
        if any(word in condition_lower for word in ["neuf", "new", "nouveau", "brand new"]):
            return "new"
        elif any(word in condition_lower for word in ["très bon état", "very good", "excellent", "comme neuf", "like new"]):
            return "like_new"
        elif any(word in condition_lower for word in ["bon état", "good", "bien", "used"]):
            return "good"
        elif any(word in condition_lower for word in ["satisfaisant", "fair", "acceptable", "worn"]):
            return "fair"

        return None

    async def search_items(self, keyword: str, category: str = "", limit: int = 50) -> List[Listing]:
        """
        Search for items on Vinted

        Args:
            keyword: Search keyword
            category: Category filter (optional)
            limit: Maximum number of results

        Returns:
            List of parsed item data
        """
        logger.info(f"Searching Vinted for: {keyword}")

        items = []

        # Vinted search URL construction
        search_params = {
            "search_text": keyword,
            "order": "newest_first"
        }

        if category:
            search_params["catalog[]"] = category

        query_string = urlencode(search_params)
        search_url = f"{self.BASE_URL}/catalog?{query_string}"

        try:
            async with ScrapingSession(scraping_config) as session:
                # Get search results page (with fallback to HTTP if Playwright fails)
                html_content = await session.get_html_with_fallback(search_url)

                # Parse items from the page
                page_items = self._parse_search_results(html_content)

                # Convert to Listing objects
                for item_dict in page_items:
                    listing = Listing(
                        source="vinted",
                        listing_id=item_dict["listing_id"],
                        title=item_dict["title"],
                        price=item_dict.get("price"),
                        currency=item_dict.get("currency", "EUR"),
                        condition_raw=item_dict.get("condition"),
                        condition_norm=self.normalize_condition_vinted(item_dict.get("condition", "")),
                        location=item_dict.get("location"),
                        seller_rating=None,  # Vinted doesn't show public ratings
                        shipping_cost=item_dict.get("shipping_cost"),
                        observed_at=datetime.now(timezone.utc),
                        is_sold=False,
                        url=item_dict.get("item_url"),
                        brand=item_dict.get("brand"),
                        size=item_dict.get("size"),
                        color=item_dict.get("color")
                    )
                    items.append(listing)

                items = items[:limit]

                logger.info(f"Found {len(items)} items on Vinted for keyword: {keyword}")

        except Exception as e:
            logger.error(f"Error searching Vinted for {keyword}: {e}")

        return items

    async def get_item_details(self, item_url: str) -> Optional[Listing]:
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
                    return Listing(
                        source="vinted",
                        listing_id=item_details["listing_id"],
                        title=item_details["title"],
                        price=item_details.get("price"),
                        currency=item_details.get("currency", "EUR"),
                        condition_raw=item_details.get("condition"),
                        condition_norm=self.normalize_condition_vinted(item_details.get("condition", "")),
                        location=item_details.get("location"),
                        seller_rating=None,
                        shipping_cost=item_details.get("shipping_cost"),
                        observed_at=datetime.now(timezone.utc),
                        is_sold=False,
                        url=item_url,
                        brand=item_details.get("brand"),
                        size=item_details.get("size"),
                        color=item_details.get("color")
                    )

                return None

        except Exception as e:
            logger.error(f"Error getting item details from {item_url}: {e}")
            return None

    def _parse_search_results(self, html_content: str) -> List[Dict[str, Any]]:
        """Parse search results HTML"""
        soup = BeautifulSoup(html_content, 'html.parser')
        items = []

        # Vinted uses different selectors, try multiple approaches
        item_selectors = [
            '[data-testid="item-card"]',  # Modern Vinted selector
            '.item',  # Legacy selector
            '[class*="item"]',  # Generic class-based selector
        ]

        item_elements = None
        for selector in item_selectors:
            item_elements = soup.select(selector)
            if item_elements:
                logger.debug(f"Found {len(item_elements)} items using selector: {selector}")
                break

        if not item_elements:
            logger.warning("No item elements found in search results")
            return items

        for element in item_elements:
            try:
                item = self._parse_item_element(element)
                if item:
                    items.append(item)
            except Exception as e:
                logger.warning(f"Error parsing item element: {e}")
                continue

        return items

    def _parse_item_element(self, element) -> Optional[Dict[str, Any]]:
        """Parse individual item element"""
        try:
            # Extract item URL
            link_element = element.find('a', href=True)
            if not link_element:
                return None

            item_url = link_element['href']
            if not item_url.startswith('http'):
                item_url = f"{self.BASE_URL}{item_url}"

            # Extract title
            title_element = element.find(['h3', '.title', '[data-testid="item-title"]'])
            title = self.scraping_utils.clean_text(title_element.get_text()) if title_element else ""

            # Extract price
            price_element = element.find(['span', '.price'], class_=lambda x: x and ('price' in x.lower()))
            price_text = price_element.get_text() if price_element else ""
            price = self.scraping_utils.extract_price(price_text)

            # Extract brand
            brand_element = element.find(['span', '.brand'], class_=lambda x: x and ('brand' in x.lower()))
            brand = self.scraping_utils.clean_text(brand_element.get_text()) if brand_element else ""

            # Extract size
            size_element = element.find(['span', '.size'], class_=lambda x: x and ('size' in x.lower()))
            size = self.scraping_utils.clean_text(size_element.get_text()) if size_element else ""

            # Extract color
            color_element = element.find(['span', '.color'], class_=lambda x: x and ('color' in x.lower()))
            color = self.scraping_utils.clean_text(color_element.get_text()) if color_element else ""

            # Extract condition
            condition_element = element.find(['span', '.condition'], class_=lambda x: x and ('condition' in x.lower() or 'état' in x.lower()))
            condition = self.scraping_utils.clean_text(condition_element.get_text()) if condition_element else ""

            # Extract location
            location_element = element.find(['span', '.location'], class_=lambda x: x and ('location' in x.lower()))
            location = self.scraping_utils.extract_location(location_element.get_text()) if location_element else ""

            # Extract shipping cost (Vinted often includes shipping)
            shipping_element = element.find(['span'], class_=lambda x: x and ('shipping' in x.lower() or 'livraison' in x.lower()))
            shipping_text = shipping_element.get_text() if shipping_element else ""
            shipping_cost = self.scraping_utils.extract_price(shipping_text) if shipping_text else 0.0

            return {
                "listing_id": self._extract_listing_id(item_url),
                "title": title,
                "price": price,
                "currency": "EUR",  # Vinted is primarily EUR
                "condition": condition,
                "location": location,
                "item_url": item_url,
                "brand": brand,
                "size": size,
                "color": color,
                "shipping_cost": shipping_cost
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
            # Vinted might use different structured data formats
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

            # Extract brand
            brand = structured_data.get('brand', {}).get('name', '') if structured_data.get('brand') else ''

            # Extract additional properties
            additional_properties = structured_data.get('additionalProperty', [])
            size = ''
            color = ''
            condition = ''

            for prop in additional_properties:
                name = prop.get('name', '').lower()
                value = prop.get('value', '')

                if 'size' in name or 'taille' in name:
                    size = value
                elif 'color' in name or 'couleur' in name:
                    color = value
                elif 'condition' in name or 'état' in name:
                    condition = value

            return {
                "listing_id": self._extract_listing_id(item_url),
                "title": title,
                "description": description,
                "price": price,
                "currency": currency,
                "brand": brand,
                "size": size,
                "color": color,
                "condition": condition,
            }

        except Exception as e:
            logger.error(f"Error parsing structured data: {e}")
            return {}

    def _parse_item_html(self, soup: BeautifulSoup, item_url: str) -> Dict[str, Any]:
        """Parse item details from HTML"""
        # Extract title
        title_element = soup.find(['h1', '.item-title'])
        title = self.scraping_utils.clean_text(title_element.get_text()) if title_element else ""

        # Extract brand
        brand_element = soup.find(['span', '.brand'], class_=lambda x: x and ('brand' in x.lower() or 'marque' in x.lower()))
        brand = self.scraping_utils.clean_text(brand_element.get_text()) if brand_element else ""

        # Extract size
        size_element = soup.find(['span', '.size'], class_=lambda x: x and ('size' in x.lower() or 'taille' in x.lower()))
        size = self.scraping_utils.clean_text(size_element.get_text()) if size_element else ""

        # Extract color
        color_element = soup.find(['span', '.color'], class_=lambda x: x and ('color' in x.lower() or 'couleur' in x.lower()))
        color = self.scraping_utils.clean_text(color_element.get_text()) if color_element else ""

        # Extract condition
        condition_element = soup.find(['span', '.condition'], class_=lambda x: x and ('condition' in x.lower() or 'état' in x.lower()))
        condition = self.scraping_utils.clean_text(condition_element.get_text()) if condition_element else ""

        # Extract price
        price_selectors = [
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

        return {
            "listing_id": self._extract_listing_id(item_url),
            "title": title,
            "price": price,
            "currency": "EUR",
            "brand": brand,
            "size": size,
            "color": color,
            "condition": condition,
        }

    def _extract_listing_id(self, url: str) -> str:
        """Extract listing ID from URL"""
        # Vinted URLs typically follow pattern: /items/ID-title
        match = re.search(r'/items/(\d+)', url)
        return match.group(1) if match else url

# Convenience functions for backward compatibility
async def fetch_vinted_listings(keyword: str, limit: int = 50) -> List[Listing]:
    """Fetch current listings from Vinted"""
    connector = VintedConnector()
    return await connector.search_items(keyword, limit=limit)

async def fetch_vinted_sold(keyword: str, limit: int = 50) -> List[Listing]:
    """
    Vinted doesn't have a direct "sold" API like eBay.
    This is a placeholder that returns recent listings as proxy for sold items.
    """
    logger.warning("Vinted doesn't provide sold items data like eBay. Returning recent listings as proxy.")
    connector = VintedConnector()
    return await connector.search_items(keyword, limit=limit)

def parse_vinted_response(response_data: Dict, is_sold: bool = False) -> List[Listing]:
    """Parse Vinted response (placeholder for compatibility)"""
    # Since we're using scraping, this function is mainly for compatibility
    return response_data if isinstance(response_data, list) else []

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
        if any(word in condition_lower for word in ["Neuf avec étiquette", "Neuf sans étiquette", "neuf", "new", "nouveau", "brand new"]):
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
                logger.debug(f"HTML Content: {html_content}")

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
        # Strategy: Find parent containers that hold item data, not just links
        # Vinted wraps items in divs/articles with the link, title, price as children
        item_selectors = [
            '[data-testid="item-card"]',  # Official item card
            '[data-testid="grid-item"]',  # Grid item wrapper
            'article',  # Article elements often contain items
            'div.feed-grid__item',  # Grid item class
            'div[class*="item"]',  # Any div with "item" in class
        ]

        item_elements = []
        for selector in item_selectors:
            elements = soup.select(selector)
            if elements:
                # Filter: Must contain a link to /items/
                item_elements = [e for e in elements if e.find('a', href=lambda h: h and '/items/' in h)]
                if item_elements:
                    logger.info(f"Found {len(item_elements)} item containers using selector: {selector}")
                    break
        
        # Fallback: If no containers found, try finding links and using their parents
        if not item_elements:
            logger.debug("No item containers found, trying fallback strategy")
            links = soup.select('a[href*="/items/"]')
            # Get parent of each link (could be the item container)
            seen_parents = set()
            for link in links:
                if '/member/' not in (link.get('href') or ''):
                    parent = link.parent
                    if parent and parent not in seen_parents:
                        item_elements.append(parent)
                        seen_parents.add(parent)
            if item_elements:
                logger.info(f"Found {len(item_elements)} item containers using fallback (link parents)")

        if not item_elements:
            logger.warning("No item elements found in search results")
            return items

        parsed_count = 0
        filtered_count = 0
        
        for element in item_elements:
            try:
                item = self._parse_item_element(element)
                if item:
                    items.append(item)
                    parsed_count += 1
                else:
                    filtered_count += 1
            except Exception as e:
                logger.warning(f"Error parsing item element: {e}")
                filtered_count += 1
                continue

        logger.info(f"Vinted parsing: {parsed_count} items parsed, {filtered_count} filtered out")
        return items

    def _validate_item_data(self, item_data: Dict[str, Any]) -> bool:
        """Validate parsed item before returning"""
        # Must have valid listing ID (numeric)
        listing_id = item_data.get("listing_id", "")
        if not listing_id or not listing_id.isdigit():
            logger.debug(f"Invalid listing_id: {listing_id}")
            return False
        
        # Must have reasonable price
        price = item_data.get("price")
        if not price or price < 0.5 or price > 10000:
            logger.debug(f"Invalid price: {price} for item {listing_id}")
            return False
        
        # Title should not be empty or just a price pattern
        title = item_data.get("title", "")
        if not title or re.match(r'^\d+[,\.]\d+\s*€?$', title) or len(title) < 3:
            logger.debug(f"Invalid title: '{title}' for item {listing_id}")
            return False
        
        return True

    def _parse_item_element(self, element) -> Optional[Dict[str, Any]]:
        """
        Parse individual item element (parent container with link, title, price as children).
        Element is now the parent container, not the link itself.
        """
        try:
            # Find the link to the item within this container
            link_element = element.find('a', href=lambda h: h and '/items/' in h)
            if not link_element:
                logger.debug("No item link found in container")
                return None
            
            item_url = link_element.get('href', '')
            
            if not item_url:
                logger.debug("Empty href in link")
                return None

            # FILTER: Skip member profiles
            if '/member/' in item_url:
                return None

            if not item_url.startswith('http'):
                item_url = f"{self.BASE_URL}{item_url}"

            # Extract item ID from URL for debugging
            item_id = self._extract_listing_id(item_url)
            
            # Get all text from container (not just the link!)
            element_text = element.get_text(separator=' ', strip=True)
            logger.debug(f"Processing item {item_id}, text preview: {element_text[:200] if element_text else 'empty'}")

            # Extract title - try multiple selectors with validation
            title = ""
            title_candidates = [
                element.select_one('[data-testid="item-title"]'),
                element.select_one('h3'),
                element.select_one('h2'),
                element.select_one('p[class*="title"]'),
                element.select_one('[class*="ItemBox_title"]'),
                element.select_one('[class*="item-title"]'),
                element.select_one('[class*="Text"]'),  # Vinted uses generic Text classes
            ]
            
            for candidate in title_candidates:
                if candidate:
                    text = self.scraping_utils.clean_text(candidate.get_text())
                    # Validate: Title shouldn't be just a price or too short
                    if text and not re.match(r'^\d+[,\.]\d+\s*€?$', text) and len(text) >= 3:
                        title = text
                        logger.debug(f"Title extracted for {item_id}: {title}")
                        break

            # Extract price with MUCH more aggressive strategies
            price = None
            
            # Strategy 1: Look for specific price elements with multiple selectors
            price_candidates = [
                element.select_one('[data-testid="item-price"]'),
                element.select_one('[class*="ItemBox_price"]'),
                element.select_one('[class*="item-price"]'),
                element.select_one('[class*="price"]'),  # Generic price class
                element.select_one('[class*="Price"]'),  # Capital P
                # Vinted often uses div/span with specific patterns
                element.find('div', string=re.compile(r'\d+[,\.]\d{2}\s*€')),
                element.find('span', string=re.compile(r'\d+[,\.]\d{2}\s*€')),
                element.find('p', string=re.compile(r'\d+[,\.]\d{2}\s*€')),
            ]
            
            for candidate in price_candidates:
                if candidate:
                    price_text = candidate.get_text(strip=True)
                    logger.debug(f"Price candidate text for {item_id}: {price_text}")
                    # Try to extract price
                    extracted_price = self.scraping_utils.extract_price(price_text)
                    if extracted_price and 0.5 <= extracted_price <= 10000:
                        price = extracted_price
                        logger.debug(f"Price extracted for {item_id}: €{price}")
                        break
            
            # Strategy 2: Scan ALL text content for price patterns
            if not price and element_text:
                logger.debug(f"Trying regex on full text for {item_id}")
                # Pattern 1: Standard European format with € symbol
                price_patterns = [
                    r'(\d{1,5})[,\.](\d{2})\s*€',  # 123,45 € or 123.45€
                    r'€\s*(\d{1,5})[,\.](\d{2})',  # € 123,45 or €123.45
                    r'(\d{1,5})\s*€',               # 123 € (no decimals)
                ]
                
                for pattern in price_patterns:
                    match = re.search(pattern, element_text)
                    if match:
                        if len(match.groups()) >= 2:
                            price_str = f"{match.group(1)}.{match.group(2)}"
                        else:
                            price_str = match.group(1)
                        
                        try:
                            price = float(price_str)
                            if 0.5 <= price <= 10000:
                                logger.debug(f"Price extracted via regex for {item_id}: €{price}")
                                break
                            else:
                                price = None
                        except ValueError:
                            continue
            
            # Strategy 3: Look in parent/sibling elements if still no price
            if not price:
                logger.debug(f"Searching parent/siblings for price for {item_id}")
                parent = element.parent
                if parent:
                    parent_text = parent.get_text(strip=True)
                    match = re.search(r'(\d{1,5})[,\.](\d{2})\s*€', parent_text)
                    if match:
                        try:
                            price = float(f"{match.group(1)}.{match.group(2)}")
                            if 0.5 <= price <= 10000:
                                logger.debug(f"Price found in parent for {item_id}: €{price}")
                        except ValueError:
                            pass

            # Extract brand
            brand = ""
            brand_element = element.find(['span', 'div'], class_=lambda x: x and 'brand' in str(x).lower())
            if brand_element:
                brand = self.scraping_utils.clean_text(brand_element.get_text())

            # Extract size
            size = ""
            size_element = element.find(['span', 'div'], class_=lambda x: x and 'size' in str(x).lower())
            if size_element:
                size = self.scraping_utils.clean_text(size_element.get_text())

            # Extract color
            color = ""
            color_element = element.find(['span', 'div'], class_=lambda x: x and 'color' in str(x).lower())
            if color_element:
                color = self.scraping_utils.clean_text(color_element.get_text())

            # Extract condition - look in element text or specific elements
            condition = ""
            element_text = element.get_text() if not price else ""  # Get text if we haven't already
            
            condition_keywords = ['neuf', 'new', 'très bon', 'very good', 'bon état', 'good', 'satisfaisant', 'fair', 'excellent']
            if element_text:
                element_lower = element_text.lower()
                for keyword in condition_keywords:
                    if keyword in element_lower:
                        condition = keyword
                        break
            
            # Also try specific condition elements
            if not condition:
                condition_element = element.find(['span', 'div'], class_=lambda x: x and ('condition' in str(x).lower() or 'état' in str(x).lower()))
                if condition_element:
                    condition = self.scraping_utils.clean_text(condition_element.get_text())

            # Extract location
            location = ""
            location_element = element.find(['span', 'div'], class_=lambda x: x and 'location' in str(x).lower())
            if location_element:
                location = self.scraping_utils.extract_location(location_element.get_text())

            # Extract shipping cost
            shipping_cost = 0.0
            shipping_element = element.find(['span', 'div'], class_=lambda x: x and ('shipping' in str(x).lower() or 'livraison' in str(x).lower()))
            if shipping_element:
                shipping_text = shipping_element.get_text()
                extracted_shipping = self.scraping_utils.extract_price(shipping_text)
                if extracted_shipping and 0 <= extracted_shipping <= 100:  # Reasonable shipping cost
                    shipping_cost = extracted_shipping

            # Build item data
            item_data = {
                "listing_id": self._extract_listing_id(item_url),
                "title": title,
                "price": price,
                "currency": "EUR",
                "condition": condition,
                "location": location,
                "item_url": item_url,
                "brand": brand,
                "size": size,
                "color": color,
                "shipping_cost": shipping_cost
            }
            
            # Validate before returning
            if not self._validate_item_data(item_data):
                return None
            
            return item_data

        except Exception as e:
            logger.error(f"Error parsing item element: {e}", exc_info=True)
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
        # Vinted URLs typically follow pattern: /items/ID-title or /items/ID
        match = re.search(r'/items/(\d+)', url)
        if match:
            return match.group(1)
        
        # Fallback: return empty string if no valid ID found
        logger.debug(f"Could not extract listing ID from URL: {url}")
        return ""

# Convenience functions for backward compatibility
async def fetch_vinted_listings(keyword: str, limit: int = 50) -> List[Listing]:
    """Fetch current listings from Vinted"""
    connector = VintedConnector()
    return await connector.search_items(keyword, limit=limit)

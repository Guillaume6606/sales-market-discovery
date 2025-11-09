"""
Connector for scraping product data from Cdiscount.

This module follows the same architecture as the provided Vinted and Back Market
connectors.  Since Cdiscount does not expose a public product search API for
consumers, this connector uses headless browsing via ``ScrapingSession`` to
render the search results pages and individual product pages.  It extracts
structured information such as title, price, condition, image URL, description,
product URL and shipping costs.

The search results pages on Cdiscount are built with React and hydrate on the
client, so simple HTTP requests to the ``view-source`` URL will not include
product information.  Instead the pages must be rendered in a browser.  Once
rendered, each product card appears as an ``<article>`` element with the
attribute ``data-e2e="offer-item"`` wrapped inside an ``<a>``.  The first
``<img>`` tag contains the product image and ``alt`` attribute holding the
title.  The price lives inside a descendant element with attribute
``data-e2e="lplr-price"``.  Product condition (e.g. "Reconditionné - Excellent
état") appears in a span with a class containing ``condition``.  Because
shipping costs are not shown on the listing cards, they must be extracted from
the product detail page.  Each detail page includes a JSON‑LD snippet with
schema.org ``Product`` data that provides the price, currency, item condition,
brand, colour, SKU and image.  Delivery information is available in a
``<div id="shippingInformations">`` container which contains text such as
"Livraison gratuite" for free shipping or a specific price.

The connector exposes two async methods:

``search_items(keyword, limit=50)``: Returns a list of ``Listing`` objects
containing basic information about each product matching the keyword on
Cdiscount.  It scrapes the search results page and extracts the URL, title,
price, condition and image.  Shipping cost is left ``None`` at this stage.

``get_item_details(item_url)``: Fetches a product page and enriches the
``Listing`` with description, normalized condition, brand, colour, size (if
available), currency and shipping cost.  It parses JSON‑LD structured data and
falls back to HTML parsing when necessary.

As with the other connectors, conditions are normalized via
``normalize_condition_cdiscount`` into standard categories (``new``,
``like_new``, ``good``, ``fair``) to allow downstream applications to handle
similar states uniformly.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
# Try to import loguru for structured logging; fall back to standard logging
try:
    from loguru import logger  # type: ignore
except ImportError:  # pragma: no cover - fallback in environments without loguru
    import logging
    logger = logging.getLogger(__name__)
    # Ensure there is at least a null handler to avoid "No handlers" warnings
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

from libs.common.scraping import ScrapingSession, ScrapingUtils, scraping_config
from libs.common.models import Listing


class CdiscountConnector:
    """Cdiscount scraping connector."""

    # Base domain for French Cdiscount.  Other locales (e.g. Belgian site) can
    # override this constant.  Cdiscount search pages follow the pattern
    # ``/search/10/<keyword>.html`` where ``<keyword>`` must be URL encoded.
    BASE_URL = "https://www.cdiscount.com"

    def __init__(self) -> None:
        self.scraping_utils = ScrapingUtils()

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------
    async def search_items(self, keyword: str, limit: int = 50) -> List[Listing]:
        """
        Search Cdiscount for a given keyword.

        Args:
            keyword: The search query.
            limit: Maximum number of items to return.

        Returns:
            A list of ``Listing`` objects.  Each listing contains basic
            information (title, price, condition, image, URL).  Description,
            brand, color and shipping costs are not available from the search
            page and must be fetched via ``get_item_details``.
        """
        logger.info(f"Searching Cdiscount for: {keyword}")
        listings: List[Listing] = []

        # Encode the keyword for use in the path.  Spaces are replaced with
        # hyphens by Cdiscount's UI, but URL encoding via ``quote`` also
        # functions correctly.  We lower case the keyword to match typical
        # search URLs.
        safe_keyword = quote(keyword.strip().lower())
        search_url = f"{self.BASE_URL}/search/10/{safe_keyword}.html"

        try:
            async with ScrapingSession(scraping_config) as session:
                 # Render the search page with Playwright.  Cdiscount requires JS
                # execution to populate product cards - HTTP fallback won't work.
                html_content = await session.get_html_with_playwright(search_url)
                logger.debug(
                    f"Downloaded search results for '{keyword}', length: {len(html_content)}"
                )
                # Parse the search results into a list of dictionaries
                items_data = self._parse_search_results(html_content)
                # Convert dictionaries to Listing objects (limit results)
                for item in items_data[:limit]:
                    listing = Listing(
                        source="cdiscount",
                        listing_id=item["listing_id"],
                        title=item["title"],
                        description=None,  # description is not available on the search page
                        price=item.get("price"),
                        currency=item.get("currency", "EUR"),
                        condition_raw=item.get("condition"),
                        condition_norm=self.normalize_condition_cdiscount(item.get("condition")),
                        location=None,
                        seller_rating=None,
                        shipping_cost=None,  # shipping is not shown on search results
                        observed_at=datetime.now(timezone.utc),
                        is_sold=False,
                        url=item.get("item_url"),
                        brand=None,
                        size=None,
                        color=None,
                    )
                    listings.append(listing)
                logger.info(
                    f"Found {len(listings)} results on Cdiscount for keyword '{keyword}'"
                )
        except Exception as exc:
            logger.error(f"Error searching Cdiscount for '{keyword}': {exc}")
        return listings

    async def get_item_details(self, item_url: str) -> Optional[Listing]:
        """
        Fetch and parse an individual Cdiscount product page.

        Args:
            item_url: Fully qualified URL of the product.

        Returns:
            A ``Listing`` instance with enriched attributes (description, brand,
            color, etc.) or ``None`` if parsing fails.
        """
        try:
            async with ScrapingSession(scraping_config) as session:
                html_content = await session.get_html_with_fallback(item_url)
            details = self._parse_item_details(html_content, item_url)
            if not details:
                return None
            listing = Listing(
                source="cdiscount",
                listing_id=details["listing_id"],
                title=details["title"],
                description=details.get("description"),
                price=details.get("price"),
                currency=details.get("currency", "EUR"),
                condition_raw=details.get("condition"),
                condition_norm=self.normalize_condition_cdiscount(details.get("condition")),
                location=None,
                seller_rating=None,
                shipping_cost=details.get("shipping_cost"),
                observed_at=datetime.now(timezone.utc),
                is_sold=False,
                url=item_url,
                brand=details.get("brand"),
                size=details.get("size"),
                color=details.get("color"),
            )
            return listing
        except Exception as exc:
            logger.error(f"Error getting Cdiscount details from {item_url}: {exc}")
            return None

    async def parse_product_page(self, item_url: str) -> Optional[Dict[str, Any]]:
        """
        Fetch and parse a Cdiscount product page to extract detailed fields.
        
        This method specifically extracts fields that are only available on the 
        product detail page (not on search results):
        - Description
        - Condition (detailed, e.g., "Reconditionné - Excellent état")
        - Brand
        - Shipping cost
        - Size (when available)
        - Color (when available)
        
        Args:
            item_url: Fully qualified URL of the product page.
            
        Returns:
            A dictionary containing extracted fields, or ``None`` if parsing fails.
            Keys include: listing_id, title, description, price, currency, brand,
            size, color, condition, shipping_cost.
            
        Example:
            >>> connector = CdiscountConnector()
            >>> details = await connector.parse_product_page("https://www.cdiscount.com/...")
            >>> print(details['description'])
            >>> print(details['brand'])
            >>> print(details['shipping_cost'])
        """
        try:
            async with ScrapingSession(scraping_config) as session:
                html_content = await session.get_html_with_fallback(item_url)
                logger.debug(f"Downloaded product page for '{item_url}', length: {len(html_content)} characters")
            
            details = self._parse_item_details(html_content, item_url)
            if not details:
                logger.warning(f"Failed to parse product page: {item_url}")
                return None
                
            logger.info(f"Successfully parsed product page: {item_url}")
            logger.debug(f"Extracted fields - Description: {bool(details.get('description'))}, "
                        f"Brand: {details.get('brand')}, "
                        f"Condition: {details.get('condition')}, "
                        f"Shipping: {details.get('shipping_cost')}")
            
            return details
        except Exception as exc:
            logger.error(f"Error parsing Cdiscount product page {item_url}: {exc}")
            return None

    # ----------------------------------------------------------------------
    # Normalization helpers
    # ----------------------------------------------------------------------
    def normalize_condition_cdiscount(self, condition_raw: Optional[str]) -> Optional[str]:
        """
        Map Cdiscount condition strings into canonical categories.

        The Cdiscount marketplace labels new products as "Neuf" (or the
        schema.org ``NewCondition``), used items as "Occasion", and
        refurbished items as "Reconditionné" with qualifiers like
        "Excellent état", "Très bon état" or "Bon état".  We map these into
        generic categories ``new``, ``like_new``, ``good``, ``fair``.  Unknown
        inputs return ``None``.
        """
        if not condition_raw:
            return None
        cond = condition_raw.lower()
        # New
        if any(kw in cond for kw in ["neuf", "newcondition", "neuf condition"]):
            return "new"
        # Refurbished / Like new (Excellent état)
        if "excellent" in cond:
            return "like_new"
        # Very good or very good state
        if "très bon" in cond or "very good" in cond:
            return "good"
        # Good state
        if "bon état" in cond or "bon" == cond.strip():
            return "fair"
        # Occasion / used
        if "occasion" in cond or "usedcondition" in cond:
            return "good"
        return None

    # ----------------------------------------------------------------------
    # Private parsing helpers
    # ----------------------------------------------------------------------
    def _parse_search_results(self, html_content: str) -> List[Dict[str, Any]]:
        """
        Parse the rendered HTML from a Cdiscount search results page.

        This function extracts product cards from the HTML and returns a list
        of dictionaries with keys: ``listing_id``, ``title``, ``price``,
        ``condition``, ``item_url`` and ``image``.  Shipping costs, brand and
        description are not available at this stage.

        Args:
            html_content: The HTML string of the rendered search page.

        Returns:
            A list of product dictionaries.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        items: List[Dict[str, Any]] = []
        
        # Debug: Log some links to understand page structure
        all_links = soup.find_all('a', href=True, limit=20)
        logger.debug(f"Sample links found on page: {[a.get('href')[:80] for a in all_links[:5]]}")
        
        # Try flexible selectors - Cdiscount changes its markup frequently
        # Product URLs typically contain "/f-" or "/p-" or "/fprt-"
        selectors = [
            'article',  # product cards
            'li',       # list items
            'div',  # any divs
        ]
        product_elements: List[Any] = []
        for selector in selectors:
            elements = soup.select(selector)
            candidates = []
            for elem in elements:
                link = elem.find('a', href=True)
                # Cdiscount product URLs contain /f- or /p- or /fprt-
                if link and any(pattern in str(link.get('href', '')) for pattern in ['/f-', '/p-', '/fprt-']):
                    candidates.append(elem)
            if candidates:
                product_elements = candidates
                logger.debug(f"Found {len(candidates)} product elements using selector '{selector}'")
                break
        
        if not product_elements:
            logger.warning(f"No product elements found on Cdiscount search page. Total articles: {len(soup.find_all('article'))}, Total links: {len(soup.find_all('a', href=True))}")
            return items
        
        for elem in product_elements:
            try:
                # Extract product URL from the link
                link = elem.find('a', href=True)
                if not link:
                    continue
                href = link['href']
                # Make URL absolute
                if not href.startswith('http'):
                    item_url = urljoin(self.BASE_URL, href)
                else:
                    item_url = href
                listing_id = self._extract_listing_id(item_url)
                # Title: use alt attribute of first image or fallback to text
                title = ""
                img = elem.find("img")
                if img and img.get("alt"):
                    title = self.scraping_utils.clean_text(img.get("alt"))
                if not title:
                    # Search for heading tag or strong text
                    h = elem.find(["h2", "h3", "h4"])
                    if h:
                        title = self.scraping_utils.clean_text(h.get_text())
                # Price: try multiple approaches
                price = None
                # Try data-e2e attribute first
                price_el = elem.select_one("[data-e2e='lplr-price'] span")
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    price = self.scraping_utils.extract_price(price_text)
                # Try spans or divs with class containing 'price'
                if price is None:
                    price_candidates = elem.find_all(['span', 'div'], class_=lambda c: c and 'price' in c.lower())
                    for cand in price_candidates:
                        extracted = self.scraping_utils.extract_price(cand.get_text(strip=True))
                        if extracted and 0.5 <= extracted <= 50000:
                            price = extracted
                            break
                # Fallback: search for price pattern in text
                if price is None:
                    text = elem.get_text(separator=" ", strip=True)
                    m = re.search(r"(\d{1,5})[,.](\d{2})\s*€", text)
                    if m:
                        try:
                            price = float(f"{m.group(1)}.{m.group(2)}")
                        except ValueError:
                            price = None
                # Condition: look for element with class containing 'condition'
                condition = None
                cond_el = elem.find(
                    lambda tag: tag.name == "span"
                    and tag.get("class")
                    and any("condition" in cls.lower() for cls in tag.get("class"))
                )
                if cond_el:
                    condition = self.scraping_utils.clean_text(cond_el.get_text())
                # Image URL
                image_url: Optional[str] = None
                if img and img.get("src"):
                    src = img.get("src")
                    if src.startswith("//"):
                        image_url = "https:" + src
                    elif src.startswith("http"):
                        image_url = src
                    else:
                        image_url = urljoin(self.BASE_URL, src)
                # Build dictionary
                item_data: Dict[str, Any] = {
                    "listing_id": listing_id,
                    "title": title or None,
                    "price": price,
                    "currency": "EUR",
                    "condition": condition,
                    "item_url": item_url,
                    "image": image_url,
                }
                # Basic validation: require title and price
                if item_data["title"] and item_data["price"] is not None:
                    items.append(item_data)
            except Exception as e:
                logger.debug(f"Error parsing search card: {e}")
                continue
        return items

    def _parse_item_details(self, html_content: str, item_url: str) -> Optional[Dict[str, Any]]:
        """
        Parse a Cdiscount product page and return detailed information.

        This function attempts to parse the JSON‑LD structured data for
        ``Product`` first, then extracts missing fields from the rendered HTML.

        Args:
            html_content: The HTML of the product page (rendered).
            item_url: URL of the product being parsed.

        Returns:
            A dictionary with keys: ``listing_id``, ``title``, ``description``,
            ``price``, ``currency``, ``brand``, ``color``, ``size``,
            ``condition``, ``shipping_cost``.  Returns ``None`` on failure.
        """
        soup = BeautifulSoup(html_content, "html.parser")
        try:
            title = ""
            description: Optional[str] = None
            price: Optional[float] = None
            currency = "EUR"
            brand = None
            size = None
            color = None
            condition = None
            shipping_cost: Optional[float] = None

            # Extract JSON-LD structured data (schema.org Product)
            json_ld_scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
            product_data: Dict[str, Any] | None = None
            for script in json_ld_scripts:
                try:
                    data = json.loads(script.string or "")
                except json.JSONDecodeError:
                    continue
                # If it's a list, pick the object with @type Product
                if isinstance(data, list):
                    for obj in data:
                        if obj.get("@type") == "Product":
                            product_data = obj
                            break
                elif isinstance(data, dict) and data.get("@type") == "Product":
                    product_data = data
                if product_data:
                    break
            if product_data:
                title = product_data.get("name", "")
                description = product_data.get("description")
                brand = (
                    product_data.get("brand", {}).get("name")
                    if isinstance(product_data.get("brand"), dict)
                    else None
                )
                color = product_data.get("color") or None
                # Extract offers
                offers = product_data.get("offers")
                if offers:
                    if isinstance(offers, list):
                        offers = offers[0]
                    raw_price = offers.get("price")
                    if raw_price:
                        try:
                            price = float(raw_price)
                        except (ValueError, TypeError):
                            price = None
                    currency = offers.get("priceCurrency", currency)
                    # Condition: schema itemCondition property or itemCondition from offers
                    item_condition = offers.get("itemCondition") or product_data.get("itemCondition")
                    if item_condition and isinstance(item_condition, str):
                        # Convert schema URL or token to human friendly string
                        # e.g., https://schema.org/NewCondition -> NewCondition
                        if "/" in item_condition:
                            condition = item_condition.split("/")[-1]
                        else:
                            condition = item_condition
                # Additional properties may include size etc.
                for prop in product_data.get("additionalProperty", []) or []:
                    name = (prop.get("name") or "").lower()
                    value = prop.get("value")
                    if not value:
                        continue
                    if "taille" in name or "size" in name:
                        size = value

            # Fallback title if not from structured data
            if not title:
                h1 = soup.find("h1")
                if h1:
                    title = self.scraping_utils.clean_text(h1.get_text())

            # Shipping cost: find the delivery info container
            shipping_div = soup.find(id="shippingInformations")
            if shipping_div:
                shipping_text = shipping_div.get_text(separator=" ", strip=True).lower()
                if "livraison" in shipping_text:
                    # If contains 'gratuite' or 'free', shipping is free
                    if "gratuit" in shipping_text or "free" in shipping_text:
                        shipping_cost = 0.0
                    else:
                        # extract first price value
                        m = re.search(r"(\d{1,3})[,.](\d{2})\s*€", shipping_text)
                        if m:
                            try:
                                shipping_cost = float(f"{m.group(1)}.{m.group(2)}")
                            except ValueError:
                                shipping_cost = None

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
                "shipping_cost": shipping_cost,
            }
        except Exception as e:
            logger.error(f"Error parsing Cdiscount item details: {e}")
            return None

    def _extract_listing_id(self, url: str) -> str:
        """
        Extract a pseudo‑unique identifier from a Cdiscount URL.

        Cdiscount product URLs typically end with ``/f-<category>-<slug>.html``.
        We use the slug (filename without extension) as a listing ID.  This is
        sufficient to identify individual products across requests.
        """
        try:
            slug = url.rstrip("/").split("/")[-1]
            # Remove query parameters
            slug = slug.split("?")[0]
            # Remove .html suffix
            if slug.endswith(".html"):
                slug = slug[:-5]
            return slug
        except Exception:
            return ""


# Convenience function
async def fetch_cdiscount_listings(keyword: str, limit: int = 50) -> List[Listing]:
    """Fetch listings from Cdiscount for the given keyword."""
    connector = CdiscountConnector()
    return await connector.search_items(keyword, limit=limit)
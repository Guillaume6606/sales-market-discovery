"""
Rakuten connector using heuristic web scraping
--------------------------------------------

This module provides a scraping connector for **Rakuten.fr** (the former
PriceMinister marketplace), focusing on extracting basic product
information—title, price, condition, image, description, URL and
shipping cost—from search queries and product detail pages.  Unlike
other marketplaces such as Cdiscount or Fnac, Rakuten employs strict
anti‑bot protection (DataDome) and dynamic JavaScript rendering.  As a
result, scraping Rakuten is inherently brittle and may require
frequent adjustments to selectors and request strategies.

### API availability

* **Affiliate/Advertising API** – Rakuten provides a *Product Search API*
  for its advertising/affiliate partners, as well as the **Rakuten
  Ichiba Item Search API** for the Japanese marketplace.  These
  APIs allow developers to search for items by keyword and retrieve
  fields such as name, price, item URL and shop information.  The
  Ichiba Item Search API, for example, accepts an `applicationId`
  parameter and returns structured JSON with fields like
  `itemName` and `itemPrice`【755791001688940†L45-L70】.  However, these
  services are intended for affiliates or for the Rakuten Ichiba
  marketplace; they are not usable to query Rakuten France (formerly
  PriceMinister) and do not expose shipping costs or item condition.
* **Seller API** – Rakuten France offers an API for merchants to
  publish products and manage offers, as documented by BeezUP.  This
  API uses XML/CSV feeds to push product data and requires credentials
  (login and token).  It is designed to transmit offers and shipping
  parameters from sellers to Rakuten【560024616614852†L42-L90】【560024616614852†L115-L129】.
  It does not provide search or consumer access, and authentication is
  required.

Because there is **no public product search API** for Rakuten France,
this connector relies on web scraping.

### HTML structure observations (heuristic)

Accessing Rakuten pages often triggers a CAPTCHA or a slider puzzle,
but when a page is rendered, the following patterns are typically
observed on a product detail page:

* **Title** – The product name appears in an `<h1>` element at the top
  of the page.
* **Price** – A `<span>` or `<div>` with a class containing `price`
  holds the current price (e.g. `product-price`, `offer-price`).
* **Condition** – Rakuten distinguishes between “Neuf” (new),
  “Occasion” (used) and “Reconditionné” (refurbished).  This label
  appears near the price, often in a `span` with class `item-condition`.
* **Image** – The main product image is contained in an `<img>` inside
  a gallery container; an OpenGraph meta tag (`og:image`) may also
  provide the URL.
* **Description** – Product descriptions appear in a `<div>` or
  `<section>` with class `product-description` or similar; some pages
  include a JSON‑LD `Product` script.
* **Shipping cost** – Delivery information is displayed near the
  purchase button, often in a `div` with class `delivery-info` or
  `shipping-cost`.  Phrases such as “Livraison gratuite” indicate
  free shipping.

These selectors are based on manual inspection and may need to be
adapted to specific categories.  If DataDome blocks access, the
connector will return empty results and log an error.

### Connector usage

The connector exposes two asynchronous methods:

* ``search_items(keyword, limit=50)`` – Performs a keyword search on
  Rakuten.fr and returns basic listings.  It attempts to fetch the
  search page at `https://fr.shopping.rakuten.com/search?q=<keyword>`
  and parse product cards.  Due to JavaScript rendering, a
  headless browser is needed.  Each card is expected to contain an
  anchor (`<a>`) leading to the product page, an image with `alt`
  attribute, a price span and, when available, a condition label.
* ``get_item_details(item_url)`` – Visits a product detail page and
  extracts the title, price, condition, description, image URL,
  currency and shipping cost using the heuristics described above.  It
  also normalizes conditions into standard categories (``new``,
  ``used``, ``refurbished``).

Because this connector relies on heuristics and untested selectors,
it should be treated as a starting point.  Real‑world scraping of
Rakuten may require session handling, solving CAPTCHAs or using
services like ScrapingBot.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

try:
    from loguru import logger  # type: ignore
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

from libs.common.scraping import ScrapingSession, ScrapingUtils, scraping_config
from libs.common.models import Listing


class RakutenConnector:
    """Rakuten scraping connector (experimental)."""

    BASE_URL = "https://fr.shopping.rakuten.com"

    def __init__(self) -> None:
        self.scraping_utils = ScrapingUtils()

    async def search_items(self, keyword: str, limit: int = 50) -> List[Listing]:
        """Search Rakuten.fr for items matching a keyword.

        This method fetches the search results page and attempts to
        extract basic information about each listing: URL, title, price,
        image and condition.  Due to heavy anti‑bot protection, the
        method may return an empty list if the page cannot be retrieved.
        """
        logger.info(f"Searching Rakuten for: {keyword}")
        listings: List[Listing] = []
        safe_keyword = quote(keyword.strip())
        search_url = f"{self.BASE_URL}/search/{safe_keyword}"
        try:
            async with ScrapingSession(scraping_config) as session:
                # Rakuten has DataDome bot protection - try Playwright first
                try:
                    html_content = await session.get_html_with_playwright(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=45000
                    )
                    # Add extra wait for AJAX content
                    await asyncio.sleep(3)
                    logger.debug(f"Downloaded search page for '{keyword}', length: {len(html_content)} characters")
                except Exception as playwright_error:
                    logger.warning(f"Playwright failed for Rakuten (likely DataDome protection): {playwright_error}")
                    logger.warning("Rakuten search failed - site has strong anti-bot protection (DataDome)")
                    return listings
            
            items_data = self._parse_search_results(html_content)
            for item in items_data[:limit]:
                listing = Listing(
                    source="rakuten",
                    listing_id=item["listing_id"],
                    title=item["title"],
                    description=None,
                    price=item.get("price"),
                    currency=item.get("currency", "EUR"),
                    condition_raw=item.get("condition"),
                    condition_norm=self.normalize_condition_rakuten(item.get("condition")),
                    location=None,
                    seller_rating=None,
                    shipping_cost=None,
                    observed_at=datetime.now(timezone.utc),
                    is_sold=False,
                    url=item.get("item_url"),
                    brand=None,
                    size=None,
                    color=None,
                )
                listings.append(listing)
            logger.info(f"Rakuten search returned {len(listings)} items for '{keyword}'")
        except Exception as exc:
            logger.error(f"Error searching Rakuten for '{keyword}': {exc}")
        return listings

    async def get_item_details(self, item_url: str) -> Optional[Listing]:
        """Retrieve detailed information from a Rakuten product page."""
        try:
            async with ScrapingSession(scraping_config) as session:
                # Rakuten requires JavaScript and has DataDome protection
                try:
                    html_content = await session.get_html_with_playwright(
                        item_url,
                        wait_until="domcontentloaded",
                        timeout=45000
                    )
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.warning(f"Playwright failed for Rakuten item details (likely DataDome): {e}")
                    return None
            
            details = self._parse_item_details(html_content, item_url)
            if not details:
                return None
            listing = Listing(
                source="rakuten",
                listing_id=details["listing_id"],
                title=details["title"],
                description=details.get("description"),
                price=details.get("price"),
                currency=details.get("currency", "EUR"),
                condition_raw=details.get("condition"),
                condition_norm=self.normalize_condition_rakuten(details.get("condition")),
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
            logger.error(f"Error getting Rakuten details from {item_url}: {exc}")
            return None

    async def parse_product_page(self, item_url: str) -> Optional[Dict[str, Any]]:
        """
        Fetch and parse a Rakuten product page to extract detailed fields.
        
        This method specifically extracts fields that are only available on the 
        product detail page (not on search results):
        - Description
        - Condition (detailed, e.g., "Neuf", "Reconditionné", "Occasion")
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
        """
        try:
            async with ScrapingSession(scraping_config) as session:
                # Rakuten requires JavaScript and has DataDome protection
                try:
                    html_content = await session.get_html_with_playwright(
                        item_url,
                        wait_until="domcontentloaded",
                        timeout=45000
                    )
                    await asyncio.sleep(2)
                    logger.debug(f"Downloaded product page for '{item_url}', length: {len(html_content)} characters")
                except Exception as e:
                    logger.warning(f"Playwright failed for Rakuten product page (likely DataDome): {e}")
                    return None
            
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
            logger.error(f"Error parsing Rakuten product page {item_url}: {exc}")
            return None

    def normalize_condition_rakuten(self, condition_raw: Optional[str]) -> Optional[str]:
        """Normalize Rakuten condition strings into canonical categories."""
        if not condition_raw:
            return None
        cond = condition_raw.lower()
        if "neuf" in cond:
            return "new"
        if "reconditionn" in cond:
            return "refurbished"
        if "occasion" in cond or "used" in cond:
            return "used"
        return None

    def _parse_search_results(self, html: str) -> List[Dict[str, Any]]:
        """Parse search results page to extract listing info (heuristic)."""
        soup = BeautifulSoup(html, "html.parser")
        items: List[Dict[str, Any]] = []
        
        # Debug: Log some links to understand page structure
        all_links = soup.find_all('a', href=True, limit=20)
        logger.debug(f"Sample links found on page: {[a.get('href')[:60] for a in all_links[:5]]}")
        
        # Try to find product cards with flexible selectors
        # Rakuten product URLs often contain '/offer/buy/' or '/product/'
        selectors = [
            'li[class*="search-result"]',
            'div[class*="product-card"]',
            'article',
            'li',  # Generic list items
            'div',  # Generic divs as last resort
        ]
        product_elements: List[Any] = []
        for selector in selectors:
            elems = soup.select(selector)
            candidates = []
            for elem in elems:
                link = elem.find('a', href=True)
                # Rakuten product URLs contain '/offer/buy/' or '/product/'
                if link and any(pattern in link.get('href', '') for pattern in ['/offer/buy/', '/product/', '/mfp/']):
                    candidates.append(elem)
            if candidates:
                product_elements = candidates
                logger.debug(f"Found {len(candidates)} product elements using selector '{selector}'")
                break
        
        if not product_elements:
            logger.warning(f"No product elements found on Rakuten search results. Total links: {len(soup.find_all('a', href=True))}")
            return items
        for elem in product_elements:
            try:
                link = elem.find('a', href=True)
                if not link:
                    continue
                item_url = link['href']
                if not item_url.startswith('http'):
                    item_url = urljoin(self.BASE_URL, item_url)
                listing_id = self._extract_listing_id(item_url)
                # Title from image alt or link text
                title = ''
                img = elem.find('img', alt=True)
                if img and img.get('alt'):
                    title = self.scraping_utils.clean_text(img['alt'])
                if not title:
                    title = self.scraping_utils.clean_text(link.get_text())
                # Price: try multiple approaches
                price = None
                # Try class containing 'price'
                price_el = elem.find(['span', 'div'], class_=lambda x: x and 'price' in x.lower())
                if price_el:
                    price = self.scraping_utils.extract_price(price_el.get_text())
                # Fallback: search all spans/divs
                if price is None:
                    for tag in elem.find_all(['span', 'div']):
                        extracted = self.scraping_utils.extract_price(tag.get_text(strip=True))
                        if extracted and 0.5 <= extracted <= 50000:
                            price = extracted
                            break
                # Condition from labels containing 'neuf', 'occasion', 'reconditionne'
                condition = None
                text = elem.get_text(separator=' ', strip=True).lower()
                for c in ['neuf', 'reconditionn', 'occasion']:
                    if c in text:
                        condition = c
                        break
                # Image URL
                image_url = None
                if img and img.get('src'):
                    image_url = img['src']
                if listing_id and title:
                    items.append({
                        "listing_id": listing_id,
                        "title": title,
                        "price": price,
                        "currency": "EUR",
                        "condition": condition,
                        "image": image_url,
                        "item_url": item_url,
                    })
            except Exception as exc:
                logger.debug(f"Error parsing Rakuten search item: {exc}")
                continue
        return items

    def _parse_item_details(self, html: str, item_url: str) -> Optional[Dict[str, Any]]:
        """Parse Rakuten product page to extract detailed information."""
        soup = BeautifulSoup(html, "html.parser")
        try:
            title_el = soup.find('h1')
            title = self.scraping_utils.clean_text(title_el.get_text()) if title_el else ''
            # Price
            price = None
            price_el = soup.find(['span', 'div'], class_=lambda x: x and 'price' in x.lower())
            if price_el:
                price = self.scraping_utils.extract_price(price_el.get_text())
            # Condition
            condition = None
            cond_el = soup.find(['span', 'div'], class_=lambda x: x and 'condition' in x.lower())
            if cond_el:
                cond_text = cond_el.get_text()
                for c in ['neuf', 'reconditionn', 'occasion']:
                    if c in cond_text.lower():
                        condition = c
                        break
            if not condition:
                page_text = soup.get_text(separator=' ', strip=True).lower()
                for c in ['neuf', 'reconditionn', 'occasion']:
                    if c in page_text:
                        condition = c
                        break
            # Description from a div with class 'product-description'
            description = None
            desc_el = soup.find('div', class_=lambda x: x and 'description' in x.lower())
            if desc_el:
                description = desc_el.get_text(separator=' ', strip=True)
            if not description:
                # Look for JSON-LD
                for script in soup.find_all('script', type='application/ld+json'):
                    try:
                        data = json.loads(script.string or '')
                        if isinstance(data, dict) and data.get('@type') == 'Product':
                            description = data.get('description')
                            break
                    except Exception:
                        continue
            # Image
            image = None
            og_img = soup.find('meta', property='og:image')
            if og_img and og_img.get('content'):
                image = og_img['content']
            if not image:
                img_el = soup.find('img', alt=lambda x: title and x and title[:10].lower() in x.lower())
                if img_el and img_el.get('src'):
                    image = img_el['src']
            # Shipping cost: search for delivery text
            shipping_cost = None
            for elem in soup.find_all(string=re.compile(r"livraison", re.I)):
                text = elem.strip().lower()
                if 'gratuit' in text:
                    shipping_cost = 0.0
                    break
                cost = self.scraping_utils.extract_price(text)
                if cost:
                    shipping_cost = cost
                    break
            listing_id = self._extract_listing_id(item_url)
            return {
                "listing_id": listing_id,
                "title": title,
                "price": price,
                "currency": "EUR",
                "condition": condition,
                "description": description,
                "brand": None,
                "size": None,
                "color": None,
                "image": image,
                "shipping_cost": shipping_cost,
            }
        except Exception as exc:
            logger.error(f"Error parsing Rakuten details: {exc}")
            return None

    def _extract_listing_id(self, url: str) -> str:
        """Extract a pseudo listing ID from the Rakuten URL."""
        # Rakuten product URLs have various patterns
        # /offer/buy/123456 or /product/123456 or /mfp/123456
        patterns = [
            r"/buy/(\d+)",
            r"/product/(\d+)",
            r"/mfp/(\d+)",
            r"/(\d{6,})",  # Any 6+ digit number as fallback
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        # Last resort: use hash of URL
        return str(hash(url))[:10]


async def fetch_rakuten_listings(keyword: str, limit: int = 50) -> List[Listing]:
    """Convenience function to fetch Rakuten listings."""
    connector = RakutenConnector()
    return await connector.search_items(keyword, limit=limit)
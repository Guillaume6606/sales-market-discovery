"""
Fnac connector using advanced web scraping
-----------------------------------------

This module implements a scraping connector for **Fnac Occasion** (the
second‑hand and refurbished section of Fnac.com).  As of 2025, Fnac does
not expose a public search API for consumers.  The only official APIs
available are the **Fnac Products API** and **Fnac Offers API**, which are
used by merchants to create product sheets, update offers and manage
orders on the marketplace【356973583931790†L170-L185】.  These seller APIs do
not provide a way to search the catalogue or retrieve listings for
second‑hand products.  Consequently, in order to obtain product data
such as title, price, condition, image, description, URL and shipping
cost, we must scrape the website.

The Fnac website is heavily protected by anti‑bot measures (DataDome
WAF) and delivers its pages via dynamic JavaScript rendering.  To
reliably extract information, this connector uses the same
architecture as the other connectors in this repository: it wraps
requests in a ``ScrapingSession`` which falls back to a headless
browser (Playwright) when necessary.  It then parses the rendered
HTML using BeautifulSoup, applying multiple selectors and heuristics to
robustly locate the desired fields.

**Key observations about Fnac product pages**

* Every product page includes a main heading (the product name) inside
  an ``<h1>`` element.  This is a reliable source for the **title**.
* For refurbished/second‑hand items, Fnac displays a table of
  conditions (``Parfait``, ``Très bon``, ``Bon``, ``Correct``) along
  with their associated prices.  The selected condition is shown just
  after the label “Reconditionné – État :”; the list of available
  conditions and prices appears on the same line【951210873113678†L70-L84】.
* The current price of the selected offer (and the new‑product price
  if it exists) appears inside a ``<span>`` element with a
  ``class`` containing ``price`` and ``userPrice``【746366696491723†L180-L237】.  This
  pattern is used to extract the **price**.
* Shipping information is rendered near the price block and contains
  the word “Livraison”.  For free shipping, the text includes
  “gratuit”【951210873113678†L165-L171】.  Otherwise a price is embedded in the
  same text.  We parse these strings to compute the **shipping cost**.
* The product description is available in the “Résumé” section of
  the page, which appears after a “Résumé” heading【951210873113678†L129-L135】.  When
  this section is collapsed, the description may also be present in
  JSON‑LD structured data under a ``<script type="application/ld+json">`` tag.  We
  attempt to parse either source.
* Product images are displayed in a gallery; the first image element
  typically represents the main image.  When an OpenGraph meta tag is
  not present, we fall back to the first ``<img>`` in the gallery or
  search the ``<img>`` whose ``alt`` attribute matches the title.

The connector exposes two asynchronous methods:

``search_items(keyword, limit=50)``
    Perform a search on Fnac for a given keyword and return a list of
    basic product listings (URL, title, price, condition and image).
    Due to Fnac’s anti‑bot measures, scraping the search results is
    inherently fragile.  This implementation tries several selectors
    (``article``, ``div`` and ``li`` elements with classes containing
    ``product`` or ``result``) to locate product cards.  If the
    anti‑bot protection blocks the page, the method will return an
    empty list and log an error.  For each card the method extracts the
    anchor URL, title (from ``img`` alt or text), price and condition
    using heuristics.  Shipping cost and description are not available
    on the search results page.

``get_item_details(item_url)``
    Fetch an individual product page and extract detailed information.
    It parses the title, price, currency, condition, description,
    brand, size, colour, image URL and shipping cost.  Condition
    strings from Fnac (“Parfait”, “Très bon”, “Bon”, “Correct”) are
    normalized into canonical categories (``new``, ``like_new``,
    ``good``, ``fair``).  Shipping cost is parsed as a float or set to
    zero when the delivery is free.

The connector returns instances of ``Listing`` (from
``libs.common.models``) for downstream processing.  See the docstring
of each method for details.
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
except ImportError:  # pragma: no cover - fallback if loguru is absent
    import logging
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

from libs.common.scraping import ScrapingSession, ScrapingUtils, scraping_config
from libs.common.models import Listing


class FnacConnector:
    """Fnac Occasion scraping connector."""

    BASE_URL = "https://www.fnac.com"

    def __init__(self) -> None:
        self.scraping_utils = ScrapingUtils()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def search_items(self, keyword: str, limit: int = 50) -> List[Listing]:
        """Search Fnac for a keyword and return basic product listings.

        Args:
            keyword: Search query string.
            limit: Maximum number of listings to return.

        Returns:
            A list of :class:`Listing` objects containing the URL, title,
            price, currency, condition and image URL for each product.
            Description, brand, colour, size and shipping cost are not
            available at this stage and will be ``None``.
        """
        logger.info(f"Searching Fnac for: {keyword}")
        listings: List[Listing] = []

        # Build the search URL.  Fnac uses a fairly simple query string
        # endpoint.  Spaces are encoded with plus signs or ``%20``.
        safe_keyword = quote(keyword.strip())
        search_url = f"{self.BASE_URL}/SearchResult/ResultList.aspx?Search={safe_keyword}"

        try:
            async with ScrapingSession(scraping_config) as session:
                html_content = await session.get_html_with_fallback(search_url)
            items_data = self._parse_search_results(html_content)
            for item in items_data[:limit]:
                listing = Listing(
                    source="fnac",
                    listing_id=item["listing_id"],
                    title=item["title"],
                    description=None,
                    price=item.get("price"),
                    currency=item.get("currency", "EUR"),
                    condition_raw=item.get("condition"),
                    condition_norm=self.normalize_condition_fnac(item.get("condition")),
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
            logger.info(f"Fnac search returned {len(listings)} items for '{keyword}'")
        except Exception as exc:
            logger.error(f"Error searching Fnac for '{keyword}': {exc}")
        return listings

    async def get_item_details(self, item_url: str) -> Optional[Listing]:
        """Get detailed information about a specific Fnac product.

        Args:
            item_url: Absolute URL to the product page on fnac.com.

        Returns:
            A populated :class:`Listing` object or ``None`` if parsing fails.
        """
        try:
            async with ScrapingSession(scraping_config) as session:
                html_content = await session.get_html_with_fallback(item_url)
            details = self._parse_item_details(html_content, item_url)
            if not details:
                return None
            listing = Listing(
                source="fnac",
                listing_id=details["listing_id"],
                title=details["title"],
                description=details.get("description"),
                price=details.get("price"),
                currency=details.get("currency", "EUR"),
                condition_raw=details.get("condition"),
                condition_norm=self.normalize_condition_fnac(details.get("condition")),
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
            logger.error(f"Error getting Fnac details from {item_url}: {exc}")
            return None

    async def parse_product_page(self, item_url: str) -> Optional[Dict[str, Any]]:
        """
        Fetch and parse a Fnac product page to extract detailed fields.
        
        This method specifically extracts fields that are only available on the 
        product detail page (not on search results):
        - Description
        - Condition (detailed, e.g., "Parfait", "Très bon")
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
            logger.error(f"Error parsing Fnac product page {item_url}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Normalization helper
    # ------------------------------------------------------------------
    def normalize_condition_fnac(self, condition_raw: Optional[str]) -> Optional[str]:
        """Normalize Fnac condition labels to canonical categories.

        Fnac uses French labels for refurbished items, for example
        "Parfait", "Très bon", "Bon" and "Correct".  These map to
        standard categories used across connectors:

        * ``Parfait`` → ``like_new``
        * ``Très bon`` → ``like_new``
        * ``Bon`` → ``good``
        * ``Correct`` → ``fair``
        * ``Neuf`` or ``Neuf scellé`` → ``new``

        If no mapping is found, return ``None``.
        """
        if not condition_raw:
            return None
        cond = condition_raw.lower()
        if "parfait" in cond:
            return "like_new"
        if "très bon" in cond or "tres bon" in cond:
            return "like_new"
        if "bon" in cond:
            return "good"
        if "correct" in cond:
            return "fair"
        if "neuf" in cond:
            return "new"
        return None

    # ------------------------------------------------------------------
    # Internal parsing helpers
    # ------------------------------------------------------------------
    def _parse_search_results(self, html: str) -> List[Dict[str, Any]]:
        """Parse the Fnac search results HTML and return a list of item dicts."""
        soup = BeautifulSoup(html, "html.parser")
        items: List[Dict[str, Any]] = []

        # Try multiple selectors because Fnac changes its markup frequently.
        selectors = [
            'article',  # common container for product items
            'li',       # list items in product grids
            'div',      # generic divs that may wrap product data
        ]
        product_elements: List[Any] = []
        for selector in selectors:
            elements = soup.select(selector)
            # Filter out elements that contain a link to a product page
            candidates = []
            for elem in elements:
                link = elem.find('a', href=True)
                if link and "/a" in link['href']:
                    candidates.append(elem)
            if candidates:
                product_elements = candidates
                break
        if not product_elements:
            logger.warning("No product elements found in Fnac search results.")
            return items

        for elem in product_elements:
            try:
                # Extract URL
                link = elem.find('a', href=True)
                if not link:
                    continue
                item_url = link['href']
                if not item_url.startswith('http'):
                    item_url = urljoin(self.BASE_URL, item_url)
                listing_id = self._extract_listing_id(item_url)

                # Extract title from the link text or image alt attribute
                title = ''
                # Option 1: alt attribute of first img
                img = elem.find('img', alt=True)
                if img and img.get('alt'):
                    title = self.scraping_utils.clean_text(img['alt'])
                # Option 2: text inside heading tags
                if not title:
                    heading = elem.find(['h3', 'h2', 'p'], string=True)
                    if heading:
                        title = self.scraping_utils.clean_text(heading.get_text())

                # Extract price: look for spans with class containing 'price'
                price = None
                price_spans = elem.find_all('span', class_=lambda x: x and 'price' in x.lower())
                for span in price_spans:
                    price_val = self.scraping_utils.extract_price(span.get_text())
                    if price_val:
                        price = price_val
                        break

                # Extract condition: search for known labels in the element text
                condition = None
                text = elem.get_text(separator=' ', strip=True).lower()
                for candidate in ["parfait", "très bon", "tres bon", "bon", "correct", "neuf"]:
                    if candidate in text:
                        condition = candidate
                        break

                # Extract image URL
                image_url = None
                if img and img.get('src'):
                    image_url = img['src']

                if not listing_id or not title:
                    continue
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
                logger.debug(f"Error parsing Fnac search item: {exc}")
                continue
        return items

    def _parse_item_details(self, html: str, item_url: str) -> Optional[Dict[str, Any]]:
        """Parse the Fnac product detail page and return a dictionary of fields."""
        soup = BeautifulSoup(html, "html.parser")
        try:
            # Title
            title_el = soup.find('h1')
            title = self.scraping_utils.clean_text(title_el.get_text()) if title_el else ''

            # Price: look for userPrice classes as documented【746366696491723†L180-L237】
            price = None
            price_candidates = soup.find_all('span', class_=lambda x: x and 'price' in x.lower())
            for span in price_candidates:
                price_val = self.scraping_utils.extract_price(span.get_text())
                if price_val:
                    price = price_val
                    break

            # Condition: parse from the "Reconditionné - État" section or general text
            condition = None
            # Search for the label "Reconditionné - État" and take the following word
            cond_label = soup.find(string=re.compile(r"Reconditionn[eé]\s*[-–]\s*État", re.I))
            if cond_label:
                # The selected condition may be in the next sibling or a nearby element
                next_el = cond_label.find_next()
                if next_el and hasattr(next_el, 'get_text'):
                    cond_text = self.scraping_utils.clean_text(next_el.get_text())
                    # Extract one of the known keywords
                    for candidate in ["Parfait", "Très bon", "Tres bon", "Bon", "Correct", "Neuf"]:
                        if candidate.lower() in cond_text.lower():
                            condition = candidate
                            break
            if not condition:
                # Fallback: search in page text for the first occurrence
                page_text = soup.get_text(separator=' ', strip=True).lower()
                for candidate in ["parfait", "très bon", "tres bon", "bon", "correct", "neuf"]:
                    if candidate in page_text:
                        condition = candidate
                        break

            # Description: look for the "Résumé" section (collapsed) or JSON‑LD
            description = None
            # Attempt to find the Résumé section by heading text
            resume_heading = soup.find(string=re.compile(r"Résumé", re.I))
            if resume_heading:
                # The description may be in the next sibling or parent
                parent = resume_heading.parent
                # Collect all text from the next siblings until the next heading
                desc_parts: List[str] = []
                for sib in parent.find_all_next(string=True, limit=10):
                    text = sib.strip()
                    if text and not re.match(r"^[#\s]*$", text):
                        # Stop if we hit another section title (e.g. "Caractéristiques")
                        if re.match(r"caract[eé]ristiques", text.lower()):
                            break
                        desc_parts.append(text)
                if desc_parts:
                    description = ' '.join(desc_parts).strip()
            if not description:
                # Look for JSON-LD structured data
                for script in soup.find_all('script', type='application/ld+json'):
                    try:
                        data = json.loads(script.string or '')
                        if isinstance(data, dict) and data.get('@type') == 'Product':
                            description = data.get('description')
                            break
                    except Exception:
                        continue

            # Brand, size and colour: attempt to parse from the characteristics section
            brand = None
            size = None
            color = None
            # The page lists characteristics in a table or list; search for known labels
            for label in soup.find_all(['strong', 'span']):
                label_text = label.get_text(strip=True).lower()
                if 'marque' in label_text or 'brand' in label_text:
                    # The value is likely in the next sibling
                    val_el = label.find_next(text=True)
                    if val_el:
                        brand = self.scraping_utils.clean_text(val_el)
                if 'taille' in label_text or 'size' in label_text:
                    val_el = label.find_next(text=True)
                    if val_el:
                        size = self.scraping_utils.clean_text(val_el)
                if 'couleur' in label_text or 'color' in label_text:
                    val_el = label.find_next(text=True)
                    if val_el:
                        color = self.scraping_utils.clean_text(val_el)

            # Image: prefer OpenGraph meta if present
            image = None
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                image = og_image['content']
            if not image:
                # Fallback: first image in gallery or matching the title
                img_el = soup.find('img', alt=lambda x: x and title and title[:10].lower() in x.lower())
                if img_el and img_el.get('src'):
                    image = img_el['src']

            # Shipping cost: parse text containing "Livraison"
            shipping_cost = None
            for elem in soup.find_all(string=re.compile(r"Livraison", re.I)):
                text = elem.strip().lower()
                if 'gratuit' in text:
                    shipping_cost = 0.0
                    break
                price_val = self.scraping_utils.extract_price(elem)
                if price_val:
                    shipping_cost = price_val
                    break

            # Listing ID: derive from the URL (e.g. '/a17312773/w-4')
            listing_id = self._extract_listing_id(item_url)

            return {
                "listing_id": listing_id,
                "title": title,
                "price": price,
                "currency": "EUR",
                "condition": condition,
                "description": description,
                "brand": brand,
                "size": size,
                "color": color,
                "image": image,
                "shipping_cost": shipping_cost,
            }
        except Exception as exc:
            logger.error(f"Error parsing Fnac details: {exc}")
            return None

    def _extract_listing_id(self, url: str) -> str:
        """Extract the Fnac product ID from the URL (e.g. a17312773)."""
        match = re.search(r"/a(\d+)", url)
        return match.group(1) if match else ""


# Convenience function for backward compatibility
async def fetch_fnac_listings(keyword: str, limit: int = 50) -> List[Listing]:
    """Fetch current listings from Fnac for a given keyword."""
    connector = FnacConnector()
    return await connector.search_items(keyword, limit=limit)
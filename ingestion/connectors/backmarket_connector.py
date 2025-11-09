"""
Back Market connector using advanced web scraping.

This connector mirrors the design of the provided Vinted connector. It uses an
asynchronous scraping session (based on Playwright with fallback) to fetch
search results from Back Market and extract individual product listings. The
connector will then parse the details page of each product to enrich the
listing data. Back Market does not expose a public product search API, so
scraping the HTML is necessary.  The selectors used below are based on
observations of Back Market's current front‑end (2025) and may require
adjustments if the site changes its structure.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, quote, urljoin

from bs4 import BeautifulSoup
from loguru import logger

from libs.common.scraping import ScrapingSession, ScrapingUtils, scraping_config
from libs.common.models import Listing


class BackmarketConnector:
    """Back Market scraping connector"""

    # Default base URL for Back Market France.  This can be overridden if
    # scraping another locale (e.g. en‑us).  The search endpoint accepts a
    # ``q`` query parameter with the search term.
    BASE_URL = "https://www.backmarket.fr"

    def __init__(self) -> None:
        self.scraping_utils = ScrapingUtils()

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------
    def normalize_condition_backmarket(self, condition_raw: str | None) -> Optional[str]:
        """Normalize Back Market condition labels to standard categories.

        Back Market typically uses labels like « Fair », « Good », « Very good »,
        « Excellent » or « Premium ».  We map them to a few generic categories
        understood by our platform.  Unknown labels return ``None``.
        """
        if not condition_raw:
            return None
        condition_lower = condition_raw.lower().strip()
        # Standard Back Market condition names
        if any(word in condition_lower for word in ["premium", "flawless", "excellent", "perfect"]):
            return "like_new"
        if "very" in condition_lower and "good" in condition_lower:
            return "like_new"
        if "good" in condition_lower:
            return "good"
        if "fair" in condition_lower or "satisfaisant" in condition_lower or "acceptable" in condition_lower:
            return "fair"
        # fall back to None
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def search_items(self, keyword: str, limit: int = 50) -> List[Listing]:
        """
        Search for items on Back Market.

        Args:
            keyword: Search keyword.
            limit: Maximum number of results to return.

        Returns:
            A list of ``Listing`` objects with basic information.
        """
        logger.info(f"Searching Back Market for: {keyword}")
        listings: List[Listing] = []

        # Construct the search URL.  Back Market uses the query string "q" for
        # search terms.  We include locale in the path ("/fr-fr/search"), though
        # the base domain already defines the country.  If you want to scrape
        # another locale, override BASE_URL accordingly.
        search_params = {"q": keyword}
        query_string = urlencode(search_params, safe=" ")
        search_url = f"{self.BASE_URL}/fr-fr/search?{query_string}"

        try:
            async with ScrapingSession(scraping_config) as session:
                # Back Market has strong bot protection - use less strict wait strategy
                # and be prepared for timeout.
                try:
                    # Use 'domcontentloaded' instead of 'networkidle' as BackMarket may never idle
                    html_content = await session.get_html_with_playwright(
                        search_url, 
                        wait_until="domcontentloaded",  # Less strict than networkidle
                        timeout=45000  # 45s timeout
                    )
                    # Add extra wait for AJAX content to load
                    await asyncio.sleep(3)
                    logger.debug(f"Downloaded search page for '{keyword}', length: {len(html_content)} characters")
                except Exception as playwright_error:
                    logger.warning(f"Playwright failed for BackMarket (likely bot protection): {playwright_error}")
                    # BackMarket has very strong bot protection - may not work without proxies/captcha solving
                    logger.warning("BackMarket search failed - site has strong anti-bot protection")
                    return listings

                # Parse the HTML to extract product containers.
                page_items = self._parse_search_results(html_content)

                # Convert dictionaries to Listing objects and normalise conditions.
                for item_dict in page_items[:limit]:
                    listing = Listing(
                        source="backmarket",
                        listing_id=item_dict["listing_id"],
                        title=item_dict["title"],
                        description=item_dict.get("description"),
                        price=item_dict.get("price"),
                        currency=item_dict.get("currency", "EUR"),
                        condition_raw=item_dict.get("condition"),
                        condition_norm=self.normalize_condition_backmarket(item_dict.get("condition")),
                        location=item_dict.get("location"),
                        seller_rating=None,  # Back Market doesn’t expose seller ratings on cards
                        shipping_cost=item_dict.get("shipping_cost"),
                        observed_at=datetime.now(timezone.utc),
                        is_sold=False,
                        url=item_dict.get("item_url"),
                        brand=item_dict.get("brand"),
                        size=item_dict.get("size"),
                        color=item_dict.get("color"),
                    )
                    listings.append(listing)

                logger.info(f"Found {len(listings)} Back Market results for keyword '{keyword}'")

        except Exception as e:
            logger.error(f"Error searching Back Market for '{keyword}': {e}")

        return listings

    async def get_item_details(self, item_url: str) -> Optional[Listing]:
        """
        Fetch detailed information for a specific product page.

        Args:
            item_url: Full URL to the product page.

        Returns:
            A ``Listing`` with detailed attributes, or ``None`` if parsing fails.
        """
        try:
            async with ScrapingSession(scraping_config) as session:
                html_content = await session.get_html_with_fallback(item_url)
                item_details = self._parse_item_details(html_content, item_url)
                if not item_details:
                    return None

                listing = Listing(
                    source="backmarket",
                    listing_id=item_details["listing_id"],
                    title=item_details["title"],
                    description=item_details.get("description"),
                    price=item_details.get("price"),
                    currency=item_details.get("currency", "EUR"),
                    condition_raw=item_details.get("condition"),
                    condition_norm=self.normalize_condition_backmarket(item_details.get("condition")),
                    location=item_details.get("location"),
                    seller_rating=None,
                    shipping_cost=item_details.get("shipping_cost"),
                    observed_at=datetime.now(timezone.utc),
                    is_sold=False,
                    url=item_url,
                    brand=item_details.get("brand"),
                    size=item_details.get("size"),
                    color=item_details.get("color"),
                )
                return listing
        except Exception as e:
            logger.error(f"Error getting Back Market item details from {item_url}: {e}")
            return None

    async def parse_product_page(self, item_url: str) -> Optional[Dict[str, Any]]:
        """
        Fetch and parse a Back Market product page to extract detailed fields.
        
        This method specifically extracts fields that are only available on the 
        product detail page (not on search results):
        - Description
        - Condition (detailed condition information)
        - Brand
        - Shipping cost
        - Size (when available)
        - Color (when available)
        
        Args:
            item_url: Fully qualified URL of the product page.
            
        Returns:
            A dictionary containing extracted fields, or ``None`` if parsing fails.
            Keys include: listing_id, title, description, price, currency, brand,
            size, color, condition, location, shipping_cost.
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
            logger.error(f"Error parsing Back Market product page {item_url}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Internal parsing helpers
    # ------------------------------------------------------------------
    def _parse_search_results(self, html_content: str) -> List[Dict[str, Any]]:
        """Parse HTML from a search results page to extract product summaries."""
        soup = BeautifulSoup(html_content, "html.parser")
        items: List[Dict[str, Any]] = []

        # Debug: Log some links to understand page structure
        all_links = soup.find_all('a', href=True, limit=20)
        logger.debug(f"Sample links found on page: {[a.get('href')[:60] for a in all_links[:5]]}")

        # Try flexible selectors - BackMarket product URLs contain '/p/'
        # First try strict selector, then fall back to flexible approach
        thumb_links = soup.select("a[data-qa='product-thumb']")
        
        if not thumb_links:
            # Fallback: find all links with '/p/' in href (product pages)
            thumb_links = [a for a in soup.find_all('a', href=True) if '/p/' in a.get('href', '')]
            logger.debug(f"Using flexible selector, found {len(thumb_links)} product links")
        else:
            logger.debug(f"Using data-qa selector, found {len(thumb_links)} product links")
        
        if not thumb_links:
            logger.warning(f"No product links found on Back Market search page. Total links: {len(soup.find_all('a', href=True))}")
            return items

        for link in thumb_links:
            try:
                href = link.get("href") or ""
                # Skip invalid or empty hrefs
                if not href:
                    continue
                # Skip anchors that do not link to product pages (rare)
                if not href.startswith("/en-us/p/") and not href.startswith("/fr-fr/p/"):
                    # allow other languages with /p/
                    if "/p/" not in href:
                        continue

                # Construct full URL using the base domain
                item_url = urljoin(self.BASE_URL, href)
                listing_id = self._extract_listing_id(item_url)

                # Attempt to locate a container element that includes the link and other data.
                # Usually the parent of the anchor or its grandparent holds the price and condition.
                container = link.parent
                # Climb up until we find an element with data‑qa containing "product-card" or until
                # we've climbed 3 levels.  This is heuristic and may need adjustment.
                level = 0
                while container and level < 3 and not container.get("data-qa", "").startswith("product-card"):
                    container = container.parent
                    level += 1
                if not container:
                    container = link.parent

                # Extract title.  Back Market often includes a <p data-qa="product-title"> or
                # the image alt attribute contains the name.
                title = ""
                title_element = container.select_one("p[data-qa='product-title']")
                if title_element:
                    title = self.scraping_utils.clean_text(title_element.get_text())
                if not title:
                    # Fallback to link's title attribute or image alt
                    title_attr = link.get("title")
                    if title_attr:
                        title = self.scraping_utils.clean_text(title_attr)
                    elif (img := link.find("img")) is not None:
                        alt = img.get("alt")
                        if alt:
                            title = self.scraping_utils.clean_text(alt)

                # Extract price from the container.  It may appear in an element with
                # data‑qa="price" or as plain text with a Euro symbol.
                price = None
                # Try specific selectors first
                price_candidates = [
                    container.select_one("[data-qa='price']"),
                    container.select_one("span.price"),
                    container.select_one("p.price"),
                    container.select_one("[class*='price']"),
                ]
                for cand in price_candidates:
                    if cand:
                        p_text = cand.get_text(strip=True)
                        extracted = self.scraping_utils.extract_price(p_text)
                        if extracted and 0.5 <= extracted <= 10000:
                            price = extracted
                            break
                # If still no price, search the text of the container using regex
                if price is None:
                    container_text = container.get_text(separator=" ", strip=True)
                    match = re.search(r"(\d{1,5})[,.](\d{2})\s*€", container_text)
                    if match:
                        price_str = f"{match.group(1)}.{match.group(2)}"
                        try:
                            price = float(price_str)
                        except ValueError:
                            price = None

                # Extract condition (Fair, Good, Very Good, Excellent, Premium).
                condition = None
                # Look for a badge with data‑qa="product-quality" or text containing known labels
                condition_el = container.select_one("[data-qa='product-quality']")
                if condition_el:
                    condition = self.scraping_utils.clean_text(condition_el.get_text())
                if not condition:
                    # Search within container text for known keywords
                    container_lower = container.get_text(separator=" ", strip=True).lower()
                    for keyword in ["premium", "flawless", "excellent", "very good", "good", "fair"]:
                        if keyword in container_lower:
                            condition = keyword
                            break

                # Attempt to extract brand, size, color (Back Market rarely displays them on cards)
                brand = ""
                size = ""
                color = ""

                # Extract shipping cost if shown.  Back Market often displays a text like
                # "Livraison gratuite" (free shipping) or a specific price.  We'll parse
                # the first price associated with "livraison" or "shipping".
                shipping_cost: float | None = None
                shipping_el = container.find(
                    lambda tag: tag.name in {"span", "div"} and (
                        (tag.get("data-qa") and "delivery" in tag.get("data-qa")) or
                        (tag.get("class") and any("shipping" in cls.lower() for cls in tag.get("class")))
                    )
                )
                if shipping_el:
                    shipping_text = shipping_el.get_text(separator=" ", strip=True)
                    # If contains "gratuit" or "free", set to 0
                    if re.search(r"gratuit|free", shipping_text, re.IGNORECASE):
                        shipping_cost = 0.0
                    else:
                        extracted_ship = self.scraping_utils.extract_price(shipping_text)
                        if extracted_ship is not None and 0 <= extracted_ship <= 100:
                            shipping_cost = extracted_ship

                # Build result dictionary and validate
                item_data: Dict[str, Any] = {
                    "listing_id": listing_id,
                    "title": title or "",
                    "price": price,
                    "currency": "EUR",
                    "condition": condition,
                    "location": None,
                    "item_url": item_url,
                    "brand": brand,
                    "size": size,
                    "color": color,
                    "shipping_cost": shipping_cost,
                }

                # Simple validation: require URL, title and price
                if item_data["title"] and item_data["price"] is not None:
                    items.append(item_data)
            except Exception as e:
                logger.debug(f"Error parsing search result: {e}")
                continue
        return items

    def _parse_item_details(self, html_content: str, item_url: str) -> Optional[Dict[str, Any]]:
        """Parse the HTML of a product page to extract detailed information."""
        soup = BeautifulSoup(html_content, "html.parser")
        try:
            # Attempt to parse JSON‑LD structured data first.  Back Market uses
            # schema.org Product markup.  This yields reliable title,
            # description, images and a base price.
            json_ld_script = soup.find("script", attrs={"type": "application/ld+json"})
            structured_data: Dict[str, Any] | None = None
            if json_ld_script and json_ld_script.string:
                try:
                    structured_data = json.loads(json_ld_script.string)
                except json.JSONDecodeError:
                    structured_data = None

            title = ""
            description = None
            price = None
            currency = "EUR"
            brand = ""
            size = ""
            color = ""
            condition = None
            images: List[str] | None = None

            if structured_data:
                # If the top level is an array, take the first object
                if isinstance(structured_data, list):
                    structured_data = structured_data[0]
                title = structured_data.get("name", "")
                description = structured_data.get("description", None)
                images = structured_data.get("image") if structured_data.get("image") else None
                offers = structured_data.get("offers", {})
                # offers may be a list of offers; use the first one
                if isinstance(offers, list):
                    offers = offers[0]
                raw_price = offers.get("price")
                if raw_price:
                    try:
                        price = float(raw_price)
                    except (ValueError, TypeError):
                        price = None
                currency = offers.get("priceCurrency", "EUR")
                # Additional properties may include brand, size, color, condition
                additional_props = structured_data.get("additionalProperty", [])
                if additional_props and isinstance(additional_props, list):
                    for prop in additional_props:
                        name = (prop.get("name") or "").lower()
                        value = prop.get("value")
                        if not value:
                            continue
                        if "brand" in name or "marque" in name:
                            brand = value
                        elif "size" in name or "taille" in name:
                            size = value
                        elif "color" in name or "couleur" in name:
                            color = value
                        elif "condition" in name or "état" in name:
                            condition = value

            # Fallbacks if structured data is missing or incomplete
            if not title:
                h1 = soup.find("h1")
                if h1:
                    title = self.scraping_utils.clean_text(h1.get_text())

            # Extract variants (conditions and prices).  As discovered during research,
            # Back Market lists different grades (e.g. Fair, Good, Excellent) in
            # containers with data‑qa values 10, 11, 12.  We'll parse the first
            # available variant if a base price isn't present.  For each variant we
            # take the condition and price from the child elements.
            if price is None:
                for q in ["10", "11", "12", "13", "14"]:
                    variant = soup.select_one(f"[data-qa='{q}']")
                    if variant:
                        try:
                            # The first child contains condition and price labels
                            first = variant.find_all(recursive=False)[0]
                            # Condition is usually the first element's text
                            cond = first.find_all(recursive=False)[0].get_text(strip=True)
                            val = first.find_all(recursive=False)[1].get_text(strip=True)
                            price_candidate = self.scraping_utils.extract_price(val)
                            if price_candidate:
                                price = price_candidate
                                condition = cond
                                break
                        except Exception:
                            continue

            # Shipping cost: look for an element with delivery/shipping info
            shipping_cost: float | None = None
            shipping_container = soup.find(
                lambda tag: tag.name in {"span", "div"} and (
                    (tag.get("data-qa") and "delivery" in tag.get("data-qa")) or
                    (tag.get("class") and any("shipping" in c.lower() for c in tag.get("class")))
                )
            )
            if shipping_container:
                text = shipping_container.get_text(separator=" ", strip=True)
                if re.search(r"gratuit|free", text, re.IGNORECASE):
                    shipping_cost = 0.0
                else:
                    extracted_ship = self.scraping_utils.extract_price(text)
                    if extracted_ship is not None and 0 <= extracted_ship <= 100:
                        shipping_cost = extracted_ship

            # Extract location if present (Back Market often shows seller country)
            location = None
            loc_el = soup.find(
                lambda tag: tag.name in {"span", "div"} and tag.get_text(strip=True).startswith("Vendu par ")
            )
            if loc_el:
                # e.g., "Vendu par France" -> we want "France"
                text = loc_el.get_text(strip=True)
                # Remove the prefix
                location = text.split(" ", 2)[-1] if " " in text else text

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
                "location": location,
                "shipping_cost": shipping_cost,
            }
        except Exception as e:
            logger.error(f"Error parsing Back Market item details: {e}")
            return None

    def _extract_listing_id(self, url: str) -> str:
        """Extract a unique listing identifier from a Back Market URL.

        Back Market product URLs typically end with a UUID.  Example:
            https://www.backmarket.fr/fr-fr/p/iphone-13-128-gb/12345678-abcd-efgh-ijkl-1234567890ab
        We'll extract the last segment after the final slash.
        """
        try:
            parts = url.rstrip("/").split("/")
            # The last part may include query parameters; strip them
            last = parts[-1].split("?")[0]
            return last
        except Exception:
            logger.debug(f"Could not extract Back Market listing ID from URL: {url}")
            return ""


# Convenience function similar to Vinted's for backward compatibility
async def fetch_backmarket_listings(keyword: str, limit: int = 50) -> List[Listing]:
    """Fetch current listings from Back Market."""
    connector = BackmarketConnector()
    return await connector.search_items(keyword, limit=limit)
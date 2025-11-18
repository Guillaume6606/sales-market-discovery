"""
Multi-stage filtering pipeline for listings.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from loguru import logger

from libs.common.models import Listing, ProductTemplate
from ingestion.ingestion import ProductTemplateSnapshot
from libs.common.llm_service import assess_listing_relevance
from libs.common.screenshot_service import capture_listing_screenshot


@dataclass
class FilteringStats:
    """Statistics about filtering results."""
    total_listings: int = 0
    passed_price: int = 0
    passed_brand: int = 0
    passed_words_avoid: int = 0
    passed_llm: int = 0
    rejected_price: int = 0
    rejected_brand: int = 0
    rejected_words_avoid: int = 0
    rejected_llm: int = 0


def _matches_price(snapshot: ProductTemplateSnapshot, listing: Listing) -> bool:
    """Check if listing price matches product template price range."""
    if listing.price is None:
        if snapshot.price_min is not None or snapshot.price_max is not None:
            return False
        return True
    if snapshot.price_min is not None and listing.price < snapshot.price_min:
        return False
    if snapshot.price_max is not None and listing.price > snapshot.price_max:
        return False
    return True


def _matches_brand(snapshot: ProductTemplateSnapshot, listing: Listing) -> bool:
    """Check if listing matches the product's brand."""
    if not snapshot.brand:
        return True
    
    brand_lower = snapshot.brand.lower()
    
    # If brand is in search term, trust search API results
    composed_term = f"{snapshot.search_query} {snapshot.brand}".strip().lower()
    if brand_lower in composed_term:
        logger.debug(f"Skipping brand filter - brand '{snapshot.brand}' already in search term")
        return True
    
    # Check listing brand field
    if listing.brand and listing.brand.lower() == brand_lower:
        return True

    # Check title
    if listing.title and brand_lower in listing.title.lower():
        return True

    return False


def _matches_words_to_avoid(snapshot: ProductTemplateSnapshot, listing: Listing) -> bool:
    """
    Check if listing contains any words to avoid.
    
    Returns:
        True if listing does NOT contain words to avoid (passes filter)
        False if listing contains words to avoid (should be rejected)
    """
    words_to_avoid = snapshot.words_to_avoid or []
    if not words_to_avoid:
        return True
    
    # Combine title and description for checking
    text_to_check = ""
    if listing.title:
        text_to_check += listing.title.lower() + " "
    # Note: Listing model doesn't have description field, but we check title
    
    # Check each word/phrase
    for word in words_to_avoid:
        if word.lower() in text_to_check:
            logger.debug(
                f"Listing '{listing.title[:50]}...' rejected: contains word to avoid '{word}'"
            )
            return False
    
    return True


def filter_listings_multi_stage(
    snapshot: ProductTemplateSnapshot,
    listings: List[Listing],
    product_template: Optional[ProductTemplate] = None,
    enable_llm: bool = False,
) -> tuple[List[Listing], FilteringStats, Dict[str, Dict[str, Any]], Dict[str, str]]:
    """
    Apply multi-stage filtering to listings.
    
    Stages:
    1. Price filter
    2. Brand filter
    3. Words-to-avoid filter
    4. LLM validation (optional)
    
    Args:
        snapshot: Product template snapshot
        listings: List of listings to filter
        product_template: Full ProductTemplate model (needed for LLM validation)
        enable_llm: Whether to enable LLM validation
        
    Returns:
        Tuple of (filtered_listings, stats)
    """
    stats = FilteringStats(total_listings=len(listings))
    
    # Stage 1: Price filter
    after_price = []
    for listing in listings:
        if _matches_price(snapshot, listing):
            after_price.append(listing)
            stats.passed_price += 1
        else:
            stats.rejected_price += 1
    
    # Stage 2: Brand filter
    after_brand = []
    for listing in after_price:
        if _matches_brand(snapshot, listing):
            after_brand.append(listing)
            stats.passed_brand += 1
        else:
            stats.rejected_brand += 1
    
    # Stage 3: Words-to-avoid filter
    after_words = []
    for listing in after_brand:
        if _matches_words_to_avoid(snapshot, listing):
            after_words.append(listing)
            stats.passed_words_avoid += 1
        else:
            stats.rejected_words_avoid += 1
    
    # Stage 4: LLM validation (if enabled)
    llm_results = {}
    screenshot_paths = {}
    
    if enable_llm and product_template and product_template.enable_llm_validation:
        logger.info(f"Running LLM validation for {len(after_words)} listings")
        final_listings = []
        
        for listing in after_words:
            # Capture screenshot if URL available
            screenshot_path = None
            if listing.url:
                try:
                    screenshot_path = capture_listing_screenshot(
                        listing.url, listing.listing_id, listing.source
                    )
                    if screenshot_path:
                        screenshot_paths[listing.listing_id] = screenshot_path
                except Exception as e:
                    logger.warning(f"Failed to capture screenshot for {listing.listing_id}: {e}")
            
            # Run LLM validation
            try:
                words_to_avoid = snapshot.words_to_avoid or []
                validation_result = assess_listing_relevance(
                    listing, screenshot_path, product_template, words_to_avoid
                )
                
                # Store validation result
                llm_results[listing.listing_id] = validation_result
                
                if validation_result.get("is_relevant", True):
                    final_listings.append(listing)
                    stats.passed_llm += 1
                else:
                    stats.rejected_llm += 1
                    logger.debug(
                        f"Listing {listing.listing_id} rejected by LLM: "
                        f"{validation_result.get('reasoning', 'No reason provided')}"
                    )
            except Exception as e:
                logger.error(f"Error in LLM validation for {listing.listing_id}: {e}")
                # On error, include listing (fail open)
                final_listings.append(listing)
                stats.passed_llm += 1
    else:
        final_listings = after_words
        stats.passed_llm = len(after_words)
    
    # Log statistics
    logger.info(
        f"Filtered {stats.total_listings} listings: "
        f"{len(final_listings)} kept, "
        f"{stats.rejected_price} rejected (price), "
        f"{stats.rejected_brand} rejected (brand), "
        f"{stats.rejected_words_avoid} rejected (words to avoid), "
        f"{stats.rejected_llm} rejected (LLM)"
    )
    
    return final_listings, stats, llm_results, screenshot_paths

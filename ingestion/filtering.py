"""
Multi-stage filtering pipeline for listings.
"""

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ingestion.relevance import RelevanceClass, classify_listing_relevance
from ingestion.schemas import ProductTemplateSnapshot
from libs.common.llm_service import assess_listing_relevance
from libs.common.models import Listing, ProductTemplate
from libs.common.screenshot_service import capture_listing_screenshot
from libs.common.settings import settings


@dataclass
class FilteringStats:
    """Statistics about filtering results."""

    total_listings: int = 0
    passed_price: int = 0
    passed_brand: int = 0
    passed_relevance: int = 0
    passed_words_avoid: int = 0
    passed_llm: int = 0
    rejected_price: int = 0
    rejected_brand: int = 0
    rejected_relevance: int = 0
    rejected_words_avoid: int = 0
    rejected_llm: int = 0
    relevance_counts: dict[str, int] = field(default_factory=dict)


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

    # Provider brand fields are stronger evidence than the search query. An
    # explicit mismatch must not be bypassed because a provider received the brand.
    if listing.brand:
        return listing.brand.casefold() == snapshot.brand.casefold()

    # Check title
    if listing.title and brand_lower in listing.title.lower():
        return True

    # Missing provider brand is inconclusive; exact-product relevance handles
    # title identity in the following stage.
    return True


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


async def filter_listings_multi_stage(
    snapshot: ProductTemplateSnapshot,
    listings: list[Listing],
    product_template: ProductTemplate | None = None,
    enable_llm: bool = False,
) -> tuple[list[Listing], FilteringStats, dict[str, dict[str, Any]], dict[str, str]]:
    """
    Apply multi-stage filtering to listings.

    Stages:
    1. Price filter
    2. Brand filter
    3. Exact-product relevance filter
    4. Words-to-avoid filter
    5. LLM validation (optional)

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

    # Stage 3: Exact-product relevance filter
    after_relevance = []
    for listing in after_brand:
        decision = classify_listing_relevance(
            snapshot.name,
            snapshot.search_query,
            listing.title or "",
        )
        classification = decision.classification.value
        stats.relevance_counts[classification] = stats.relevance_counts.get(classification, 0) + 1
        vision_candidate = (
            settings.vision_enabled
            and not settings.vision_shadow_mode
            and not listing.is_sold
            and decision.classification == RelevanceClass.UNCERTAIN
            and len(after_relevance) < settings.vision_batch_size
        )
        if decision.is_relevant or vision_candidate:
            after_relevance.append(listing)
            stats.passed_relevance += 1
        else:
            stats.rejected_relevance += 1
            logger.debug(
                "Listing '{}' rejected for relevance: {} ({})",
                (listing.title or "")[:50],
                classification,
                decision.reason,
            )

    # Stage 4: Words-to-avoid filter
    after_words = []
    for listing in after_relevance:
        if _matches_words_to_avoid(snapshot, listing):
            after_words.append(listing)
            stats.passed_words_avoid += 1
        else:
            stats.rejected_words_avoid += 1

    # Stage 5: LLM validation (if enabled)
    llm_results = {}
    screenshot_paths = {}

    if settings.vision_enabled and not settings.vision_shadow_mode:
        final_listings = after_words
        stats.passed_llm = 0
        llm_results = {
            listing.listing_id: {"pipeline": "vision", "status": "pending"}
            for listing in after_words
            if not listing.is_sold
        }
    elif enable_llm and product_template and product_template.enable_llm_validation:
        logger.info(f"Running LLM validation for {len(after_words)} listings")
        final_listings = []

        for listing in after_words:
            # Capture screenshot if URL available
            screenshot_path = None
            if listing.url:
                try:
                    screenshot_path = await capture_listing_screenshot(
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

                if validation_result.get("is_relevant") is True:
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
                llm_results[listing.listing_id] = {
                    "classification": "uncertain",
                    "is_relevant": False,
                    "confidence": 0.0,
                    "reasoning": f"LLM validation failed: {e}",
                    "flags": ["validation_error"],
                }
                stats.rejected_llm += 1
    else:
        final_listings = after_words
        stats.passed_llm = len(after_words)

    # Log statistics
    logger.info(
        f"Filtered {stats.total_listings} listings: "
        f"{len(final_listings)} kept, "
        f"{stats.rejected_price} rejected (price), "
        f"{stats.rejected_brand} rejected (brand), "
        f"{stats.rejected_relevance} rejected (relevance), "
        f"{stats.rejected_words_avoid} rejected (words to avoid), "
        f"{stats.rejected_llm} rejected (LLM)"
    )

    return final_listings, stats, llm_results, screenshot_paths

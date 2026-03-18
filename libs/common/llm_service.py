"""
LLM Service for listing validation using Google Gemini via Vertex AI.
"""

import json
import os
import re
from typing import Any

from google import genai
from google.genai.types import Part
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from libs.common.models import Listing, ProductTemplate
from libs.common.settings import settings

_client_cache: genai.Client | None = None


def get_genai_client() -> genai.Client | None:
    """Return a cached Vertex AI genai client."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    if not settings.llm_enabled:
        return None
    try:
        _client_cache = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
        )
        return _client_cache
    except Exception as e:
        logger.error("Failed to initialize Vertex AI client: {}", e)
        return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def assess_listing_relevance(
    listing: Listing,
    screenshot_path: str | None,
    product_template: ProductTemplate,
    words_to_avoid: list[str],
) -> dict[str, Any]:
    """
    Assess listing relevance using Gemini vision API.

    Args:
        listing: The listing to validate
        screenshot_path: Path to screenshot image (optional)
        product_template: Product template with description and criteria
        words_to_avoid: List of words/phrases that should cause rejection

    Returns:
        Dict with keys:
            - is_relevant: bool
            - confidence: float (0-1)
            - reasoning: str
            - flags: List[str] (any issues found)
    """
    client = get_genai_client()
    if not client:
        logger.warning("LLM validation disabled, skipping assessment")
        return {
            "is_relevant": True,
            "confidence": 0.0,
            "reasoning": "LLM validation disabled",
            "flags": [],
        }

    try:
        product_desc = product_template.description or product_template.name
        price_range = ""
        if product_template.price_min or product_template.price_max:
            min_price = (
                f"€{product_template.price_min}" if product_template.price_min else "unlimited"
            )
            max_price = (
                f"€{product_template.price_max}" if product_template.price_max else "unlimited"
            )
            price_range = f"Expected price range: {min_price} - {max_price}"

        words_to_avoid_text = ""
        if words_to_avoid:
            words_to_avoid_text = (
                f"\n\nWORDS TO AVOID (reject if found): {', '.join(words_to_avoid)}"
            )

        prompt_text = f"""You are analyzing a marketplace listing to determine if it matches a product template.

PRODUCT TEMPLATE:
- Name: {product_template.name}
- Description: {product_desc}
- Brand: {product_template.brand or "Not specified"}
- Search Query: {product_template.search_query}
{price_range}

LISTING DETAILS:
- Title: {listing.title}
- Price: {listing.price} {listing.currency}
- Condition: {listing.condition_raw or "Not specified"}
- Source: {listing.source}
{words_to_avoid_text}

TASK:
1. Determine if this listing is relevant to the product template
2. Check if any words to avoid are present (in title or description)
3. Verify the listing matches the product description and brand
4. Assess if the price is reasonable for this product

Respond in JSON format:
{{
    "is_relevant": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation",
    "flags": ["list", "of", "any", "issues"]
}}

If words to avoid are found, set is_relevant to false and add them to flags."""

        content_parts: list[Any] = []

        if screenshot_path and os.path.exists(screenshot_path):
            try:
                with open(screenshot_path, "rb") as f:
                    img_bytes = f.read()
                content_parts.append(Part.from_bytes(data=img_bytes, mime_type="image/png"))
            except Exception as e:
                logger.warning("Failed to load screenshot {}: {}", screenshot_path, e)

        content_parts.append(prompt_text)

        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=content_parts,
            config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        )

        response_text = response.text.strip()

        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                except json.JSONDecodeError:
                    result = _parse_response_fallback(response_text)
            else:
                result = _parse_response_fallback(response_text)

        result.setdefault("is_relevant", True)
        result.setdefault("confidence", 0.5)
        result.setdefault("reasoning", response_text[:200])
        result.setdefault("flags", [])
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))

        logger.info(
            "LLM validation for listing {}: relevant={}, confidence={:.2f}",
            listing.listing_id,
            result["is_relevant"],
            result["confidence"],
        )

        return result

    except Exception as e:
        logger.error("Error in LLM assessment: {}", e, exc_info=True)
        return {
            "is_relevant": True,
            "confidence": 0.0,
            "reasoning": f"Error during validation: {str(e)}",
            "flags": ["validation_error"],
        }


def _parse_response_fallback(response_text: str) -> dict[str, Any]:
    """Fallback parser for non-JSON responses."""
    result = {
        "is_relevant": True,
        "confidence": 0.5,
        "reasoning": response_text[:200],
        "flags": [],
    }

    rejection_keywords = ["not relevant", "does not match", "incorrect", "wrong product"]
    if any(keyword in response_text.lower() for keyword in rejection_keywords):
        result["is_relevant"] = False
        result["confidence"] = 0.7

    return result

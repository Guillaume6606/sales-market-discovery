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

from ingestion.relevance import RelevanceClass
from libs.common.models import Listing, ProductTemplate
from libs.common.settings import settings

_client_cache: genai.Client | None = None


def get_genai_client() -> genai.Client | None:
    """Return a cached genai client.

    Uses API key auth when GEMINI_API_KEY is set (simpler, no GCP project required).
    Falls back to Vertex AI Application Default Credentials otherwise.
    """
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    if not settings.llm_enabled:
        return None
    try:
        if settings.gemini_api_key:
            _client_cache = genai.Client(api_key=settings.gemini_api_key)
        else:
            _client_cache = genai.Client(
                vertexai=True,
                project=settings.gcp_project_id,
                location=settings.gcp_location,
            )
        return _client_cache
    except Exception as e:
        logger.error("Failed to initialize Gemini client: {}", e)
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
            "classification": RelevanceClass.UNCERTAIN.value,
            "is_relevant": False,
            "confidence": 0.0,
            "reasoning": "LLM validation disabled",
            "flags": ["validation_unavailable"],
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
Classify the item being sold as exactly one of:
- exact_device: the exact requested working product
- device_bundle: the exact requested working product plus accessories
- accessory: accessories only, including controllers, cases, batteries, mounts, or games
- parts_broken: broken, repair, or parts-only product
- wrong_variant: a different model, generation, capacity, or variant
- uncertain: the title and image do not establish what is being sold

Retain working devices sold with accessories. A lens is the product, not an accessory,
when the product template itself targets that exact lens. Reject mixed-device lots.

Respond in JSON format:
{{
    "classification": "exact_device/device_bundle/accessory/parts_broken/wrong_variant/uncertain",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation",
    "flags": ["list", "of", "any", "issues"]
}}

If words to avoid are found, use parts_broken or accessory as appropriate and add them to flags."""

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
                "temperature": 0,
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

        classification = str(result.get("classification", "")).strip().lower()
        valid_classes = {member.value for member in RelevanceClass}
        if classification not in valid_classes:
            classification = RelevanceClass.UNCERTAIN.value
            result.setdefault("flags", []).append("invalid_classification")
        result["classification"] = classification
        result["is_relevant"] = classification in {
            RelevanceClass.EXACT_DEVICE.value,
            RelevanceClass.DEVICE_BUNDLE.value,
        }
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
            "classification": RelevanceClass.UNCERTAIN.value,
            "is_relevant": False,
            "confidence": 0.0,
            "reasoning": f"Error during validation: {str(e)}",
            "flags": ["validation_error"],
        }


def _parse_response_fallback(response_text: str) -> dict[str, Any]:
    """Fallback parser for non-JSON responses."""
    result = {
        "classification": RelevanceClass.UNCERTAIN.value,
        "is_relevant": False,
        "confidence": 0.0,
        "reasoning": response_text[:200],
        "flags": [],
    }

    return result

"""
LLM Service for listing validation using Google Gemini via LangChain.
"""

import json
import os
import re
from typing import Any

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from libs.common.models import Listing, ProductTemplate
from libs.common.settings import settings

try:
    from langchain_core.messages import HumanMessage
    from langchain_google_genai import ChatGoogleGenerativeAI

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    logger.warning("LangChain dependencies not available. LLM validation will be disabled.")


def _is_llm_enabled() -> bool:
    """Check if LLM service is properly configured and enabled."""
    if not LANGCHAIN_AVAILABLE:
        return False
    if not settings.llm_enabled:
        return False
    if not settings.gemini_api_key:
        logger.warning("Gemini API key not configured")
        return False
    return True


_llm_client_cache: Any = None


def _get_llm_client() -> ChatGoogleGenerativeAI | None:
    """Initialize and return Gemini LLM client (cached singleton)."""
    global _llm_client_cache
    if _llm_client_cache is not None:
        return _llm_client_cache
    if not _is_llm_enabled():
        return None
    try:
        os.environ["GOOGLE_API_KEY"] = settings.gemini_api_key
        _llm_client_cache = ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            temperature=0.1,
        )
        return _llm_client_cache
    except Exception as e:
        logger.error(f"Failed to initialize Gemini client: {e}")
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
    if not _is_llm_enabled():
        logger.warning("LLM validation disabled, skipping assessment")
        return {
            "is_relevant": True,
            "confidence": 0.0,
            "reasoning": "LLM validation disabled",
            "flags": [],
        }

    client = _get_llm_client()
    if not client:
        return {
            "is_relevant": True,
            "confidence": 0.0,
            "reasoning": "LLM client not available",
            "flags": [],
        }

    try:
        # Build prompt with product context
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

        messages = []

        # Add image if screenshot available
        if screenshot_path and os.path.exists(screenshot_path):
            try:
                import base64

                with open(screenshot_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                image_url = f"data:image/png;base64,{b64}"
                human_message = HumanMessage(
                    content=[
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ]
                )
                messages.append(human_message)
            except Exception as e:
                logger.warning(f"Failed to load screenshot {screenshot_path}: {e}")
                messages.append(HumanMessage(content=prompt_text))
        else:
            messages.append(HumanMessage(content=prompt_text))

        # Call Gemini API
        response = client.invoke(messages)

        # Parse response
        response_text = response.content if hasattr(response, "content") else str(response)

        # Try to extract JSON from response
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

        # Ensure required fields
        result.setdefault("is_relevant", True)
        result.setdefault("confidence", 0.5)
        result.setdefault("reasoning", response_text[:200])
        result.setdefault("flags", [])

        # Clamp confidence
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))

        logger.info(
            f"LLM validation for listing {listing.listing_id}: "
            f"relevant={result['is_relevant']}, confidence={result['confidence']:.2f}"
        )

        return result

    except Exception as e:
        logger.error(f"Error in LLM assessment: {e}", exc_info=True)
        return {
            "is_relevant": True,  # Default to accepting on error
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

    # Try to detect rejection keywords
    rejection_keywords = ["not relevant", "does not match", "incorrect", "wrong product"]
    if any(keyword in response_text.lower() for keyword in rejection_keywords):
        result["is_relevant"] = False
        result["confidence"] = 0.7

    return result

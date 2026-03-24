"""Structured LLM prompt for listing enrichment and response parsing."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

EXPECTED_ACCESSORIES: dict[str, list[str]] = {
    "electronics": ["charger", "cable", "earbuds", "documentation", "SIM tool"],
    "watches": ["box", "papers", "extra links", "warranty card"],
    "clothing": [],
    "gaming": ["controller", "cables", "power supply", "documentation"],
}

SCORE_KEYS: list[str] = [
    "urgency_score",
    "accessories_completeness",
    "photo_quality_score",
    "listing_quality_score",
    "condition_confidence",
    "fakeness_probability",
    "seller_motivation_score",
]

ALL_REQUIRED_KEYS: list[str] = [
    "urgency_score",
    "urgency_keywords",
    "has_original_box",
    "has_receipt_or_invoice",
    "accessories_included",
    "accessories_completeness",
    "photo_quality_score",
    "listing_quality_score",
    "condition_confidence",
    "fakeness_probability",
    "seller_motivation_score",
]


def build_enrichment_prompt(
    title: str,
    description: str | None,
    condition_raw: str | None,
    price: float,
    currency: str,
    category: str | None,
    brand: str | None,
    pmn: float | None,
    photo_urls: list[str],
    days_since_posted: int | None,
) -> str:
    """Build a structured prompt for LLM listing enrichment.

    Constructs a zero-shot prompt that asks the model to analyse a marketplace
    listing and return a fixed JSON schema with urgency, accessories,
    photo/listing quality, condition confidence, fakeness probability, and
    seller motivation scores.

    Args:
        title: Listing title as scraped from the marketplace.
        description: Free-text description, or None when absent.
        condition_raw: Condition label as stated by the seller, or None.
        price: Listed price as a float.
        currency: ISO currency code (e.g. ``"EUR"``).
        category: Product category slug used to look up expected accessories.
        brand: Brand name, or None when unknown.
        pmn: Price of Market Normal in the same currency, or None when not yet
            computed.
        photo_urls: URLs of listing photos (used for photo count hint only).
        days_since_posted: Number of days the listing has been live, or None
            when unknown.

    Returns:
        A prompt string ready to be sent to an LLM.
    """
    cat = (category or "other").lower()
    accessories = EXPECTED_ACCESSORIES.get(cat, [])

    if accessories:
        accessories_hint = f"Expected accessories for {cat}: {', '.join(accessories)}"
    else:
        accessories_hint = f"No standard accessories expected for this category ({cat})."

    pmn_text = (
        f"PMN (market normal price): €{pmn:.2f}"
        if pmn is not None
        else "PMN: not available (no market reference yet)"
    )
    dom_text = (
        f"Days on market: {days_since_posted}"
        if days_since_posted is not None
        else "Days on market: unknown"
    )
    photo_text = f"Number of photos: {len(photo_urls)}" if photo_urls else "No photos available"

    return f"""Analyze this marketplace listing and return a JSON object.

## Listing Data
- **Title:** {title}
- **Description:** {description or "(no description)"}
- **Stated condition:** {condition_raw or "(not stated)"}
- **Price:** {price} {currency}
- **Brand:** {brand or "(unknown)"}
- {pmn_text}
- {dom_text}
- {photo_text}
- {accessories_hint}

## Return this exact JSON structure:
{{
  "urgency_score": <float 0.0-1.0>,
  "urgency_keywords": [<urgency keywords found>],
  "has_original_box": <true/false/null>,
  "has_receipt_or_invoice": <true/false/null>,
  "accessories_included": [<accessories mentioned>],
  "accessories_completeness": <float 0.0-1.0>,
  "photo_quality_score": <float 0.0-1.0>,
  "listing_quality_score": <float 0.0-1.0>,
  "condition_confidence": <float 0.0-1.0>,
  "fakeness_probability": <float 0.0-1.0>,
  "seller_motivation_score": <float 0.0-1.0>
}}

Return ONLY the JSON object, no additional text."""


def parse_enrichment_response(raw_response: str) -> dict | None:  # type: ignore[type-arg]
    """Parse and validate an LLM enrichment response.

    Strips optional markdown code fences, deserialises JSON, validates that all
    required keys are present, and clamps every score field to ``[0.0, 1.0]``.

    Args:
        raw_response: Raw text returned by the LLM.

    Returns:
        A validated and normalised dictionary on success, or ``None`` when the
        response cannot be parsed or is structurally invalid.
    """
    try:
        text = raw_response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        data: dict = json.loads(text)  # type: ignore[type-arg]
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse enrichment response as JSON")
        return None

    missing = [k for k in ALL_REQUIRED_KEYS if k not in data]
    if missing:
        logger.warning("Enrichment response missing keys: %s", missing)
        return None

    for key in SCORE_KEYS:
        if data[key] is not None:
            data[key] = max(0.0, min(1.0, float(data[key])))

    if not isinstance(data.get("urgency_keywords"), list):
        data["urgency_keywords"] = []
    if not isinstance(data.get("accessories_included"), list):
        data["accessories_included"] = []

    return data

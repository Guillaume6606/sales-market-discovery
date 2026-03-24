"""Shared condition normalization across all marketplace connectors."""

from __future__ import annotations

import unicodedata


def _strip_accents(text: str) -> str:
    """Remove accent characters for matching.

    Args:
        text: Input string potentially containing accented characters.

    Returns:
        String with accent combining characters removed.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_condition(raw: str | None) -> str | None:
    """Normalize raw condition string to a standard category.

    Accepts French and English condition labels from any marketplace connector
    and maps them to one of: ``new``, ``like_new``, ``good``, ``fair``, or
    ``None`` when the input cannot be recognized.

    Matching is case-insensitive and accent-insensitive. More specific patterns
    are checked before shorter, more generic ones to avoid false positives (e.g.
    ``"tres bon etat"`` is checked before ``"bon etat"``).

    Args:
        raw: Raw condition string from a marketplace (may be ``None``).

    Returns:
        One of ``"new"``, ``"like_new"``, ``"good"``, ``"fair"``, or ``None``.
    """
    if not raw or not raw.strip():
        return None

    text = _strip_accents(raw.strip().lower())

    # Patterns are ordered from most-specific to least-specific.
    # like_new is checked before new because phrases such as "comme neuf" and
    # "like new" contain the substring "neuf"/"new" which would otherwise match
    # the new tier first.
    like_new_patterns = [
        "tres bon etat",
        "very good condition",
        "comme neuf",
        "like new",
        "excellent",
        "mint",
    ]
    new_patterns = [
        "brand new",
        "neuf avec etiquette",
        "neuf sans etiquette",
        "nouveau",
        "bnib",
        "nib",
        "neuf",
        "new",
    ]
    good_patterns = [
        "bon etat",
        "very good",
        "good",
        "bien",
        "used",
    ]
    fair_patterns = [
        "etat satisfaisant",
        "satisfaisant",
        "acceptable",
        "fair",
        "poor",
        "worn",
    ]

    for pattern in like_new_patterns:
        if pattern in text:
            return "like_new"
    for pattern in new_patterns:
        if pattern in text:
            return "new"
    for pattern in good_patterns:
        if pattern in text:
            return "good"
    for pattern in fair_patterns:
        if pattern in text:
            return "fair"

    return None

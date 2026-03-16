from dataclasses import dataclass, field

from libs.common.models import Listing


@dataclass
class ValidationStats:
    total: int = 0
    passed: int = 0
    rejected_price: int = 0
    rejected_title: int = 0
    missing_price: int = 0  # passed validation but price is None
    missing_title: int = 0  # passed validation but title is empty/whitespace-only
    rejected_reasons: dict[str, int] = field(default_factory=dict)


def validate_listing(listing: Listing) -> str | None:
    """Return None if valid, rejection reason string if invalid.

    Note: price=None is intentionally allowed — some listings have price
    discovered later via updates or marketplace-specific fields.
    """
    if listing.price is not None:
        if listing.price <= 0:
            return "price_non_positive"
        if listing.price > 50000:
            return "price_too_high"

    if not listing.title or not listing.title.strip():
        return "empty_title"

    return None


def validate_listings(listings: list[Listing]) -> tuple[list[Listing], ValidationStats]:
    """Validate a batch of listings, returning valid ones and stats."""
    stats = ValidationStats(total=len(listings))
    valid: list[Listing] = []

    for listing in listings:
        reason = validate_listing(listing)
        if reason is None:
            valid.append(listing)
            stats.passed += 1
            if listing.price is None:
                stats.missing_price += 1
        else:
            stats.rejected_reasons[reason] = stats.rejected_reasons.get(reason, 0) + 1
            if reason.startswith("price"):
                stats.rejected_price += 1
            elif reason == "empty_title":
                stats.rejected_title += 1

    return valid, stats

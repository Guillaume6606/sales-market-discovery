"""
Shared utility functions for the Market Discovery platform.
"""

from decimal import Decimal
from typing import Any


def decimal_to_float(value: Decimal | float | Any | None) -> float | None:
    """Convert a Decimal (or any numeric) to float, returning None for None or unconvertible values."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

"""Smoke tests — marketplace connectors.

Each test calls the real connector against the live marketplace API with a
minimal limit.  Results may legitimately be empty (rate limits, API key not
set, marketplace returning zero hits) so an empty list is accepted; what is
*not* accepted is an exception or a list containing objects that violate the
``Listing`` contract.
"""

from typing import Any

import pytest

from ingestion.connectors.ebay import fetch_ebay_listings
from ingestion.connectors.leboncoin_api import fetch_leboncoin_api_listings
from ingestion.connectors.vinted import fetch_vinted_listings
from libs.common.models import Listing, ProductTemplate


def _validate_listings(listings: list[Any], expected_source: str) -> None:
    """Assert structural correctness of a connector result list.

    Args:
        listings: The list returned by a connector fetch function.
        expected_source: The ``source`` field value expected on every item
            (e.g. ``"ebay"``, ``"leboncoin"``, ``"vinted"``).
    """
    assert isinstance(listings, list), (
        f"Connector for {expected_source!r} must return a list, got {type(listings).__name__}"
    )
    for item in listings:
        assert isinstance(item, Listing), (
            f"Each element must be a Listing instance; got {type(item).__name__}"
        )
        assert item.title, f"Listing {item.listing_id!r} has an empty title"
        assert item.price is not None and item.price > 0, (
            f"Listing {item.listing_id!r} has invalid price {item.price!r}"
        )
        assert item.source == expected_source, (
            f"Expected source {expected_source!r}, got {item.source!r} "
            f"for listing {item.listing_id!r}"
        )


@pytest.mark.asyncio
async def test_ebay_connector(known_product: ProductTemplate) -> None:
    """eBay connector must return a list; each element must satisfy the Listing contract."""
    listings = await fetch_ebay_listings(known_product.search_query, limit=5)
    _validate_listings(listings, "ebay")


@pytest.mark.asyncio
async def test_leboncoin_connector(known_product: ProductTemplate) -> None:
    """LeBonCoin connector must return a list; each element must satisfy the Listing contract."""
    listings = await fetch_leboncoin_api_listings(known_product.search_query, limit=5)
    _validate_listings(listings, "leboncoin")


@pytest.mark.asyncio
async def test_vinted_connector(known_product: ProductTemplate) -> None:
    """Vinted connector must return a list; each element must satisfy the Listing contract."""
    listings = await fetch_vinted_listings(known_product.search_query, limit=5)
    _validate_listings(listings, "vinted")

"""Smoke tests — data quality validation after ingestion.

These tests verify that the ingestion pipeline produces clean, well-formed
observations: no null prices, no empty titles, prices within configured bounds,
valid source identifiers, and at least one fresh observation per product.

All tests depend on `ingestion_result` to guarantee that ingestion has run
before any assertions are evaluated.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from libs.common.models import ListingObservation, ProductTemplate

VALID_SOURCES: frozenset[str] = frozenset({"ebay", "leboncoin", "vinted"})


def test_no_null_prices(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict[str, Any],  # noqa: ARG001
) -> None:
    """Recent observations for the known product must not have a null price."""
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    null_price_count: int = (
        db_session.query(ListingObservation)
        .filter(
            ListingObservation.product_id == known_product_id,
            ListingObservation.observed_at >= one_hour_ago,
            ListingObservation.price.is_(None),
        )
        .count()
    )
    assert null_price_count == 0, (
        f"Found {null_price_count} recent observation(s) with NULL price "
        f"for product {known_product_id}"
    )


def test_no_empty_titles(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict[str, Any],  # noqa: ARG001
) -> None:
    """Recent observations for the known product must not have a null or empty title."""
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    empty_title_count: int = (
        db_session.query(ListingObservation)
        .filter(
            ListingObservation.product_id == known_product_id,
            ListingObservation.observed_at >= one_hour_ago,
            (ListingObservation.title.is_(None)) | (ListingObservation.title == ""),
        )
        .count()
    )
    assert empty_title_count == 0, (
        f"Found {empty_title_count} recent observation(s) with empty/NULL title "
        f"for product {known_product_id}"
    )


def test_prices_within_bounds(
    db_session: Session,
    known_product: ProductTemplate,
    ingestion_result: dict[str, Any],  # noqa: ARG001
) -> None:
    """Prices observed in the last hour must respect the product's configured price bounds.

    Skips when neither `price_min` nor `price_max` is configured on the product template.
    """
    price_min = known_product.price_min
    price_max = known_product.price_max

    if price_min is None and price_max is None:
        pytest.skip("No price bounds configured for this product — nothing to validate")

    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    product_id: str = str(known_product.product_id)

    base_query = db_session.query(ListingObservation).filter(
        ListingObservation.product_id == product_id,
        ListingObservation.price.isnot(None),
        ListingObservation.observed_at >= one_hour_ago,
    )

    if price_min is not None:
        below_min_count: int = base_query.filter(
            ListingObservation.price < float(price_min)
        ).count()
        assert below_min_count == 0, (
            f"Found {below_min_count} recent observation(s) with price < price_min "
            f"({price_min}) for product {product_id}"
        )

    if price_max is not None:
        above_max_count: int = base_query.filter(
            ListingObservation.price > float(price_max)
        ).count()
        assert above_max_count == 0, (
            f"Found {above_max_count} recent observation(s) with price > price_max "
            f"({price_max}) for product {product_id}"
        )


def test_valid_sources(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict[str, Any],  # noqa: ARG001
) -> None:
    """Recent source values for the known product must be in the allowed set."""
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    rows: list[tuple[str]] = (
        db_session.query(ListingObservation.source)
        .filter(
            ListingObservation.product_id == known_product_id,
            ListingObservation.observed_at >= one_hour_ago,
        )
        .distinct()
        .all()
    )

    observed_sources: set[str] = {row[0] for row in rows if row[0] is not None}
    invalid_sources: set[str] = observed_sources - VALID_SOURCES

    assert not invalid_sources, (
        f"Unexpected source value(s) found for product {known_product_id}: {invalid_sources}. "
        f"Allowed sources: {VALID_SOURCES}"
    )


def test_recent_observations(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict[str, Any],  # noqa: ARG001
) -> None:
    """At least one observation for the known product must have been recorded in the last hour."""
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)

    recent_count: int = (
        db_session.query(ListingObservation)
        .filter(
            ListingObservation.product_id == known_product_id,
            ListingObservation.observed_at >= one_hour_ago,
        )
        .count()
    )

    assert recent_count >= 1, (
        f"No observations found within the last hour for product {known_product_id}. "
        f"Ingestion may have stalled or the product was not recently processed."
    )

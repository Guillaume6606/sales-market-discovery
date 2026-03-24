"""Smoke tests — PMN computation and daily metrics infrastructure.

These tests trigger or verify PMN (Price of Market Normal) computation and
validate that results are persisted correctly in the database. They are
intentionally lenient: the goal is to exercise the infrastructure path,
not to assert precise numeric outcomes.

All tests depend on `ingestion_result` (via `pmn_result`) to guarantee that
observations exist before PMN computation is attempted.
"""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from libs.common.models import MarketPriceNormal, ProductDailyMetrics


def test_pmn_computation_runs(pmn_result: dict[str, Any], known_product_id: str) -> None:
    """compute_pmn_for_product must complete without error and return a positive PMN value."""
    assert pmn_result is not None, "compute_pmn_for_product returned None"

    status = pmn_result.get("status")

    # "insufficient_data" is a legitimate outcome for sparse products
    if status == "insufficient_data":
        pytest.skip(
            f"Not enough observations to compute PMN for product {known_product_id} "
            f"(got {pmn_result.get('price_count', '?')} prices, need at least 3)"
        )

    assert status == "success", (
        f"PMN computation did not succeed for product {known_product_id}: {pmn_result}"
    )

    pmn_value = pmn_result.get("pmn")
    assert pmn_value is not None, "PMN result is missing the 'pmn' key"
    assert float(pmn_value) > 0, f"Expected pmn > 0, got {pmn_value} for product {known_product_id}"


def test_pmn_stored_in_db(
    db_session: Session,
    known_product_id: str,
    pmn_result: dict[str, Any],  # noqa: ARG001
) -> None:
    """A MarketPriceNormal record must exist and be internally consistent."""
    db_session.expire_all()

    record: MarketPriceNormal | None = (
        db_session.query(MarketPriceNormal)
        .filter(MarketPriceNormal.product_id == known_product_id)
        .first()
    )

    assert record is not None, (
        f"No MarketPriceNormal record found for product {known_product_id} after PMN computation"
    )
    assert record.pmn is not None, "MarketPriceNormal.pmn must not be NULL"
    assert float(record.pmn) > 0, f"Expected pmn > 0, got {record.pmn}"

    assert record.pmn_low is not None, "MarketPriceNormal.pmn_low must not be NULL"
    assert record.pmn_high is not None, "MarketPriceNormal.pmn_high must not be NULL"
    assert float(record.pmn_low) <= float(record.pmn), (
        f"pmn_low ({record.pmn_low}) must be <= pmn ({record.pmn})"
    )
    assert float(record.pmn) <= float(record.pmn_high), (
        f"pmn ({record.pmn}) must be <= pmn_high ({record.pmn_high})"
    )

    assert record.last_computed_at is not None, (
        "MarketPriceNormal.last_computed_at must not be NULL"
    )


def test_daily_metrics_exist(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict[str, Any],  # noqa: ARG001
) -> None:
    """At least one ProductDailyMetrics row must exist for the known product.

    When a row with a non-null liquidity_score is found, it must be non-negative.
    This test is lenient — it validates infrastructure presence, not numeric precision.
    """
    rows: list[ProductDailyMetrics] = (
        db_session.query(ProductDailyMetrics)
        .filter(ProductDailyMetrics.product_id == known_product_id)
        .all()
    )

    assert len(rows) >= 1, (
        f"No ProductDailyMetrics records found for product {known_product_id}. "
        "Ensure compute_all_product_metrics() has been called at least once."
    )

    for row in rows:
        if row.liquidity_score is not None:
            assert float(row.liquidity_score) >= 0, (
                f"liquidity_score must be >= 0, got {row.liquidity_score} "
                f"on date {row.date} for product {known_product_id}"
            )
            assert float(row.liquidity_score) <= 100, (
                f"liquidity_score must be <= 100, got {row.liquidity_score} "
                f"on date {row.date} for product {known_product_id}"
            )

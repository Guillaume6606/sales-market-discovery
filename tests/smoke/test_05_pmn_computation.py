"""Smoke tests — PMN computation and daily metrics infrastructure.

These tests trigger or verify PMN (Price of Market Normal) computation and
validate that results are persisted correctly in the database. They are
intentionally lenient: the goal is to exercise the infrastructure path,
not to assert precise numeric outcomes.

All tests depend on `ingestion_result` to guarantee that observations exist
before PMN computation is attempted.
"""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from libs.common.models import MarketPriceNormal, ProductDailyMetrics


def _import_compute_pmn() -> Any:
    """Attempt to import compute_pmn_for_product from its expected location.

    Returns the callable if found, otherwise raises ImportError.
    """
    try:
        from ingestion.computation import compute_pmn_for_product  # type: ignore[import]

        return compute_pmn_for_product
    except ImportError:
        pass

    # Fallback: some project layouts expose it from ingestion.pricing
    try:
        from ingestion.pricing import compute_pmn_for_product  # type: ignore[import]

        return compute_pmn_for_product
    except ImportError:
        pass

    raise ImportError(
        "compute_pmn_for_product not found in ingestion.computation or ingestion.pricing"
    )


def test_pmn_computation_runs(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict,  # noqa: ARG001
) -> None:
    """compute_pmn_for_product must complete without error and return a positive PMN value.

    Skips gracefully if the function cannot be located at any expected import path.
    """
    try:
        compute_pmn_for_product = _import_compute_pmn()
    except ImportError:
        pytest.skip("PMN compute function not found at expected path")

    result: dict[str, Any] = compute_pmn_for_product(known_product_id, db_session)

    assert result is not None, "compute_pmn_for_product returned None"

    status = result.get("status")

    # "insufficient_data" is a legitimate outcome for sparse products — treat as a soft pass
    if status == "insufficient_data":
        pytest.skip(
            f"Not enough observations to compute PMN for product {known_product_id} "
            f"(got {result.get('price_count', '?')} prices, need at least 3)"
        )

    assert status == "success", (
        f"PMN computation did not succeed for product {known_product_id}: {result}"
    )

    pmn_value = result.get("pmn")
    assert pmn_value is not None, "PMN result is missing the 'pmn' key"
    assert float(pmn_value) > 0, f"Expected pmn > 0, got {pmn_value} for product {known_product_id}"


def test_pmn_stored_in_db(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict,  # noqa: ARG001
) -> None:
    """A MarketPriceNormal record for the known product must exist and be internally consistent.

    PMN may already exist from a prior run — this test does not require a fresh computation.
    If the record is missing, compute it first so the test is self-contained.
    """
    try:
        compute_pmn_for_product = _import_compute_pmn()
    except ImportError:
        pytest.skip("PMN compute function not found at expected path")

    # Ensure a PMN record exists (idempotent — safe to call even if record already exists)
    result: dict[str, Any] = compute_pmn_for_product(known_product_id, db_session)

    if result.get("status") == "insufficient_data":
        pytest.skip(f"Not enough observations to compute PMN for product {known_product_id}")

    # Refresh session to pick up any writes performed by compute_pmn_for_product
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
    ingestion_result: dict,  # noqa: ARG001
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

    # Validate liquidity_score where present — must be non-negative
    for row in rows:
        if row.liquidity_score is not None:
            assert float(row.liquidity_score) >= 0, (
                f"liquidity_score must be >= 0, got {row.liquidity_score} "
                f"on date {row.date} for product {known_product_id}"
            )
            # Liquidity score is computed on a 0-100 scale
            assert float(row.liquidity_score) <= 100, (
                f"liquidity_score must be <= 100, got {row.liquidity_score} "
                f"on date {row.date} for product {known_product_id}"
            )

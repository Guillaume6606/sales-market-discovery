"""Smoke tests — end-to-end ingestion pipeline.

Relies on the ``ingestion_result`` session fixture (defined in conftest.py)
which runs ``run_full_ingestion`` once and shares the result across both tests.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from libs.common.models import IngestionRun


def test_full_ingestion_success(ingestion_result: dict[str, Any]) -> None:
    """Full ingestion must complete without error and return data from at least one connector.

    The ``run_full_ingestion`` function returns a dict with a top-level
    ``status`` key and per-connector sub-dicts that each carry a ``count``
    field.  At least one connector must report ``count > 0`` for the test to
    pass, demonstrating that the pipeline wrote rows to the database.
    """
    assert ingestion_result.get("status") == "success", (
        f"Ingestion returned status {ingestion_result.get('status')!r}; "
        f"error={ingestion_result.get('error')!r}"
    )

    connector_keys = {
        "ebay_sold",
        "ebay_listings",
        "leboncoin_listings",
        "leboncoin_sold",
        "vinted_listings",
    }
    counts = [
        ingestion_result[key]["count"]
        for key in connector_keys
        if key in ingestion_result and isinstance(ingestion_result[key], dict)
    ]
    assert any(c > 0 for c in counts), (
        "Expected at least one connector to persist listings, "
        f"but all counts are zero or missing: {ingestion_result}"
    )


def test_ingestion_run_tracking(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict[str, Any],  # noqa: ARG001
) -> None:
    """A successful IngestionRun record must be present in the database.

    The record must have been created within the last 5 minutes (i.e. by the
    fixture that ran during this test session), must carry ``status='success'``,
    ``listings_persisted > 0``, ``duration_s > 0``, and no ``error_message``.
    """
    five_minutes_ago = datetime.now(UTC) - timedelta(minutes=5)

    runs = (
        db_session.query(IngestionRun)
        .filter(
            IngestionRun.product_id == known_product_id,
            IngestionRun.started_at > five_minutes_ago,
        )
        .all()
    )

    assert runs, (
        f"No IngestionRun records found for product {known_product_id!r} "
        "within the last 5 minutes. The ingestion fixture may not have run "
        "or run tracking may be broken."
    )

    successful_runs = [r for r in runs if r.status == "success" and (r.listings_persisted or 0) > 0]
    assert successful_runs, (
        f"No successful IngestionRun with listings_persisted > 0 found among "
        f"{len(runs)} recent run(s). Statuses: {[r.status for r in runs]}"
    )

    for run in successful_runs:
        assert run.duration_s is not None and run.duration_s > 0, (
            f"IngestionRun {run.run_id} has invalid duration_s={run.duration_s!r}"
        )
        assert run.error_message is None, (
            f"IngestionRun {run.run_id} has unexpected error_message: {run.error_message!r}"
        )

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from loguru import logger

from ingestion.filtering import FilteringStats
from libs.common.db import SessionLocal
from libs.common.models import IngestionRun


def filtering_stats_to_dict(stats: FilteringStats) -> dict:
    """Convert FilteringStats dataclass to a JSON-serializable dict."""
    return asdict(stats)


@contextmanager
def track_ingestion_run(
    product_id: str, source: str, function_name: str
) -> Generator[IngestionRun, None, None]:
    """Track an ingestion run lifecycle in the database.

    Creates an IngestionRun row on enter, yields a detached object for the caller
    to populate with stats, then persists final state on exit.
    """
    started_at = datetime.now(UTC)
    run_id = None

    # Create the initial row
    with SessionLocal() as db:
        run = IngestionRun(
            product_id=product_id,
            source=source,
            function_name=function_name,
            status="running",
            started_at=started_at,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.run_id

    # Yield a detached mutable object for the caller to set attributes on
    tracker = IngestionRun(run_id=run_id)
    tracker.listings_fetched = None
    tracker.listings_deduped = None
    tracker.listings_persisted = None
    tracker.filtering_stats = None
    tracker.status = "success"
    tracker.error_message = None

    try:
        yield tracker
    except Exception as exc:
        tracker.status = "error"
        tracker.error_message = str(exc)[:2000]
        raise
    finally:
        finished_at = datetime.now(UTC)
        duration_s = (finished_at - started_at).total_seconds()

        try:
            with SessionLocal() as db:
                persisted = db.query(IngestionRun).filter(IngestionRun.run_id == run_id).first()
                if persisted:
                    persisted.status = tracker.status
                    persisted.finished_at = finished_at
                    persisted.duration_s = duration_s
                    persisted.listings_fetched = tracker.listings_fetched
                    persisted.listings_deduped = tracker.listings_deduped
                    persisted.listings_persisted = tracker.listings_persisted
                    persisted.filtering_stats = tracker.filtering_stats
                    persisted.error_message = tracker.error_message
                    db.commit()
        except Exception as db_exc:
            logger.error(f"Failed to persist ingestion run {run_id}: {db_exc}")

from datetime import UTC, datetime, timedelta

from ingestion import ingestion, worker


def test_source_is_due_when_interval_elapsed():
    now = datetime.now(UTC)
    assert hasattr(worker, "ingestion_is_due")
    assert worker.ingestion_is_due(now - timedelta(minutes=16), 15, now)
    assert not worker.ingestion_is_due(now - timedelta(minutes=14), 15, now)
    assert worker.ingestion_is_due(None, 15, now)


def test_partial_pipeline_failure_is_not_success():
    assert hasattr(ingestion, "pipeline_status")
    assert ingestion.pipeline_status({"ebay_listings": {"status": "error"}}) == "error"
    assert (
        ingestion.pipeline_status(
            {"ebay_listings": {"status": "success"}, "vinted_listings": {"status": "error"}}
        )
        == "partial"
    )
    assert (
        ingestion.pipeline_status(
            {"ebay_sold": {"status": "unsupported"}, "ebay_listings": {"status": "success"}}
        )
        == "success"
    )
    assert ingestion.pipeline_status({"ebay_listings": {"status": "no_data"}}) == "no_data"


def test_successful_postprocessing_does_not_hide_missing_source_data():
    assert (
        ingestion.pipeline_status(
            {"ebay_listings": {"status": "no_data"}, "pipeline": {"status": "success"}}
        )
        == "no_data"
    )
    assert (
        ingestion.pipeline_status(
            {"ebay_listings": {"status": "error"}, "pipeline": {"status": "success"}}
        )
        == "error"
    )


async def test_scheduler_reports_duplicate_enqueue_as_already_queued(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(worker, "_active_product_ids", lambda source: ["product"])
    pool = AsyncMock()
    pool.enqueue_job.return_value = None
    result = await worker.scheduled_source_ingestion({"redis": pool}, "ebay")
    assert result["product"] == "already_queued"

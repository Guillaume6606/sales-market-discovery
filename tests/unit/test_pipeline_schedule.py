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
    pool.exists.return_value = 0
    pool.enqueue_job.return_value = None
    result = await worker.scheduled_source_ingestion({"redis": pool}, "ebay")
    assert result["product"] == "already_queued"


async def test_scheduler_does_not_enqueue_during_provider_cooldown(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    active_products = Mock(return_value=["product"])
    monkeypatch.setattr(worker, "_active_product_ids", active_products)
    pool = AsyncMock()
    pool.exists.return_value = 1
    result = await worker.scheduled_source_ingestion({"redis": pool}, "vinted")
    assert result == {"status": "cooldown", "source": "vinted"}
    pool.enqueue_job.assert_not_awaited()
    active_products.assert_not_called()


async def test_cashconverters_runs_full_pipeline(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from ingestion.constants import SUPPORTED_PROVIDERS

    assert "cashconverters" in SUPPORTED_PROVIDERS
    monkeypatch.setattr(
        ingestion,
        "_load_product_snapshot",
        lambda _: SimpleNamespace(
            name="Sony", product_id="p", category_name="Photo", providers=["cashconverters"]
        ),
    )
    fetch = AsyncMock(return_value={"status": "success", "count": 2})
    monkeypatch.setattr(ingestion, "ingest_cashconverters_listings", fetch)
    monkeypatch.setattr(ingestion, "update_product_metrics", lambda _: None)
    finish = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(ingestion, "finish_product_pipeline", finish)
    result = await ingestion._run_full_ingestion(
        "p", {"cashconverters_listings": 5}, ["cashconverters"]
    )
    fetch.assert_awaited_once_with("p", 5)
    finish.assert_awaited_once_with("p", ["cashconverters"])
    assert result["status"] == "success"

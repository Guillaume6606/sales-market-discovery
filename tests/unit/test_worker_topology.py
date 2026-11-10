from unittest.mock import AsyncMock, Mock

import pytest

from ingestion import topology, worker
from libs.common.settings import settings

DEFAULT_CRONS = [
    "deliver_pending_alerts",
    "mark_stale_listings",
    "scheduled_ebay_ingestion",
    "scheduled_leboncoin_ingestion",
    "scheduled_cashconverters_ingestion",
    "scheduled_vinted_ingestion",
    "scheduled_computation",
    "check_system_health",
    "run_enrichment_batch",
    "run_scoring_batch",
]
DEFAULT_FUNCTIONS = [
    "ping",
    "ingest_scheduled_product",
    "deliver_pending_alerts",
    "scheduled_ebay_ingestion",
    "scheduled_leboncoin_ingestion",
    "scheduled_vinted_ingestion",
    "scheduled_cashconverters_ingestion",
    "trigger_ebay_sold_ingestion",
    "trigger_ebay_listings_ingestion",
    "trigger_leboncoin_listings_ingestion",
    "trigger_leboncoin_sold_ingestion",
    "trigger_vinted_listings_ingestion",
    "trigger_full_ingestion",
    "scheduled_computation",
    "trigger_product_computation",
    "trigger_batch_computation",
    "validate_listing_with_llm",
    "capture_listing_screenshot_task",
    "process_opportunity_alerts",
    "mark_stale_listings",
    "check_system_health",
    "audit_ingestion_sample",
    "run_on_demand_audit",
    "run_enrichment_batch",
    "run_scoring_batch",
]


def _cron_names(jobs: list) -> list[str]:
    return [job.coroutine.__name__ for job in jobs]


def _function_names(functions: list) -> list[str]:
    return [function.__name__ for function in functions]


@pytest.fixture
def lbc_at_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "remote_providers", "leboncoin")


@pytest.fixture
def remote_role(monkeypatch: pytest.MonkeyPatch, lbc_at_home: None) -> None:
    monkeypatch.setattr(settings, "worker_role", "remote")


def test_default_single_worker_is_unchanged() -> None:
    assert settings.worker_role == "primary"
    assert settings.remote_providers == ""
    assert worker.WorkerSettings.queue_name == "arq:queue"
    assert worker.WorkerSettings.health_check_key == "worker:heartbeat"
    assert _cron_names(worker.WorkerSettings.cron_jobs) == DEFAULT_CRONS
    assert _function_names(worker.WorkerSettings.functions) == DEFAULT_FUNCTIONS
    assert topology.expected_roles() == ["primary"]
    assert topology.worker_providers("primary") == ["ebay", "leboncoin", "vinted", "cashconverters"]


async def test_default_full_ingestion_runs_every_source_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(worker, "run_full_ingestion", run)
    pool = AsyncMock()
    await worker.trigger_full_ingestion({"redis": pool}, "p", 5, 6)
    assert run.await_args.args[2] == ["ebay", "leboncoin", "vinted", "cashconverters"]
    pool.enqueue_job.assert_not_awaited()


def test_primary_drops_remote_provider_crons_and_keeps_global_jobs(lbc_at_home: None) -> None:
    names = _cron_names(worker.worker_cron_jobs("primary"))
    assert names == [name for name in DEFAULT_CRONS if name != "scheduled_leboncoin_ingestion"]
    assert _function_names(worker.worker_functions("primary")) == DEFAULT_FUNCTIONS


def test_remote_worker_runs_only_its_provider_crons(lbc_at_home: None) -> None:
    assert _cron_names(worker.worker_cron_jobs("remote")) == ["scheduled_leboncoin_ingestion"]
    functions = _function_names(worker.worker_functions("remote"))
    for global_job in (
        "deliver_pending_alerts",
        "mark_stale_listings",
        "scheduled_computation",
        "check_system_health",
        "run_enrichment_batch",
        "run_scoring_batch",
        "audit_ingestion_sample",
    ):
        assert global_job not in functions
    assert "ingest_scheduled_product" in functions
    assert "trigger_full_ingestion" in functions
    assert "trigger_leboncoin_listings_ingestion" in functions


def test_remote_worker_listens_on_its_own_queue_and_heartbeat(lbc_at_home: None) -> None:
    assert topology.QUEUES["remote"] != topology.QUEUES["primary"]
    assert topology.HEARTBEAT_KEYS["remote"] != topology.HEARTBEAT_KEYS["primary"]
    assert topology.expected_roles() == ["primary", "remote"]
    assert topology.provider_role("leboncoin") == "remote"
    assert topology.provider_role("ebay") == "primary"


async def test_remote_worker_heartbeat_uses_its_own_key(lbc_at_home: None) -> None:
    from arq.worker import Worker

    pool = AsyncMock()
    pool.zcard.return_value = 0
    remote = Worker(
        functions=worker.worker_functions("remote"),
        cron_jobs=worker.worker_cron_jobs("remote"),
        redis_pool=pool,
        queue_name=topology.QUEUES["remote"],
        health_check_key=topology.HEARTBEAT_KEYS["remote"],
        health_check_interval=worker.WorkerSettings.health_check_interval,
        handle_signals=False,
    )
    remote.job_counter = remote.max_jobs
    await remote._poll_iteration()
    assert pool.psetex.await_args.args[0] == "worker:heartbeat:remote"
    pool.zcard.assert_awaited_with("arq:queue:remote")


def test_remote_role_without_providers_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="REMOTE_PROVIDERS"):
        worker.worker_cron_jobs("remote")


def test_unknown_remote_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "remote_providers", "leboncoin, amazon")
    with pytest.raises(ValueError, match="amazon"):
        topology.remote_providers()


async def test_primary_forwards_remote_provider_job_to_remote_queue(
    monkeypatch: pytest.MonkeyPatch, lbc_at_home: None
) -> None:
    run = AsyncMock()
    monkeypatch.setattr(worker, "run_full_ingestion", run)
    pool = AsyncMock()
    pool.enqueue_job.return_value = Mock(job_id="j1")
    result = await worker.ingest_scheduled_product({"redis": pool}, "p", "leboncoin")
    run.assert_not_awaited()
    pool.enqueue_job.assert_awaited_once_with(
        "ingest_scheduled_product", "p", "leboncoin", _queue_name="arq:queue:remote"
    )
    assert result["status"] == "forwarded"


async def test_primary_splits_full_ingestion_between_workers(
    monkeypatch: pytest.MonkeyPatch, lbc_at_home: None
) -> None:
    run = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(worker, "run_full_ingestion", run)
    pool = AsyncMock()
    pool.enqueue_job.return_value = Mock(job_id="j2")
    result = await worker.trigger_full_ingestion(
        {"redis": pool}, "p", 5, 6, sources=["ebay", "leboncoin"]
    )
    assert run.await_args.args[2] == ["ebay"]
    pool.enqueue_job.assert_awaited_once_with(
        "trigger_full_ingestion", "p", 5, 6, sources=["leboncoin"], _queue_name="arq:queue:remote"
    )
    assert result["forwarded"]["job_id"] == "j2"


async def test_primary_forwards_provider_specific_trigger(
    monkeypatch: pytest.MonkeyPatch, lbc_at_home: None
) -> None:
    sold = AsyncMock()
    monkeypatch.setattr(worker, "ingest_leboncoin_sold", sold)
    pool = AsyncMock()
    pool.enqueue_job.return_value = Mock(job_id="j3")
    result = await worker.trigger_leboncoin_sold_ingestion({"redis": pool}, "p", 7)
    sold.assert_not_awaited()
    pool.enqueue_job.assert_awaited_once_with(
        "trigger_leboncoin_sold_ingestion", "p", 7, _queue_name="arq:queue:remote"
    )
    assert result["status"] == "forwarded"


async def test_remote_worker_never_forwards_back(
    monkeypatch: pytest.MonkeyPatch, remote_role: None
) -> None:
    run = AsyncMock()
    monkeypatch.setattr(worker, "run_full_ingestion", run)
    pool = AsyncMock()
    result = await worker.ingest_scheduled_product({"redis": pool}, "p", "ebay")
    assert result == {"status": "skipped", "reason": "provider_not_served", "provider": "ebay"}
    run.assert_not_awaited()
    pool.enqueue_job.assert_not_awaited()


async def test_remote_worker_runs_its_provider_locally(
    monkeypatch: pytest.MonkeyPatch, remote_role: None
) -> None:
    run = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(worker, "run_full_ingestion", run)
    pool = AsyncMock()
    result = await worker.trigger_full_ingestion(
        {"redis": pool}, "p", 5, 6, sources=["ebay", "leboncoin"]
    )
    assert run.await_args.args[2] == ["leboncoin"]
    pool.enqueue_job.assert_not_awaited()
    assert result["status"] == "success"


async def test_system_health_alerts_when_remote_worker_is_offline(
    monkeypatch: pytest.MonkeyPatch, lbc_at_home: None
) -> None:
    db = Mock()
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []
    session = Mock()
    session.__enter__ = Mock(return_value=db)
    session.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(worker, "SessionLocal", Mock(return_value=session))
    alert = AsyncMock()
    monkeypatch.setattr(worker, "send_system_alert", alert)
    pool = AsyncMock()
    pool.exists.return_value = 0
    result = await worker.check_system_health({"redis": pool})
    pool.exists.assert_awaited_once_with("worker:heartbeat:remote")
    failing = alert.await_args.args[2]
    assert [entry["name"] for entry in failing] == ["leboncoin"]
    assert result["failing_connectors"] == 1


async def test_system_health_skips_heartbeat_lookup_in_single_worker_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Mock()
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []
    session = Mock()
    session.__enter__ = Mock(return_value=db)
    session.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(worker, "SessionLocal", Mock(return_value=session))
    alert = AsyncMock()
    monkeypatch.setattr(worker, "send_system_alert", alert)
    pool = AsyncMock()
    result = await worker.check_system_health({"redis": pool})
    pool.exists.assert_not_awaited()
    alert.assert_not_awaited()
    assert result == {"stale_products": 0, "failing_connectors": 0}

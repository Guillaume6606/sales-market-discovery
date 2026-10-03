from unittest.mock import AsyncMock

from arq.worker import Worker

from ingestion.worker import WorkerSettings, ping


async def test_worker_refreshes_readiness_with_all_job_slots_occupied():
    pool = AsyncMock()
    pool.zcard.return_value = 5
    worker = Worker(
        functions=[ping],
        redis_pool=pool,
        max_jobs=WorkerSettings.max_jobs,
        health_check_key=getattr(WorkerSettings, "health_check_key", None),
        health_check_interval=getattr(WorkerSettings, "health_check_interval", 3600),
        handle_signals=False,
    )
    worker.job_counter = worker.max_jobs

    await worker._poll_iteration()

    key, ttl_ms, payload = pool.psetex.await_args.args
    assert key == "worker:heartbeat"
    assert ttl_ms <= 61000
    assert b"queued=5" in payload
    pool.zrangebyscore.assert_not_awaited()

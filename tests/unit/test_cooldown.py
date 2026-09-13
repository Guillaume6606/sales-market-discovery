"""Shared (Redis-backed) source cooldown: connector sets it, scheduler honours it."""

from unittest.mock import AsyncMock

import httpx
import pytest

from ingestion import worker
from ingestion.connectors import vinted_api
from ingestion.connectors.vinted_transport import ReliableVintedScraper
from libs.common.cooldown import cooldown_key, source_cooldown_active


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = (value, ex)

    async def exists(self, key: str) -> int:
        return int(key in self.store)


class BrokenRedis:
    async def set(self, *args, **kwargs):
        raise ConnectionError("redis down")

    async def exists(self, *args, **kwargs):
        raise ConnectionError("redis down")


def _forbidden_factory(calls: list[httpx.Request]):
    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403)

    def scraper_factory(baseurl, *, config):
        return ReliableVintedScraper(baseurl, config={"transport": httpx.MockTransport(respond)})

    return scraper_factory


async def test_forbidden_sets_shared_cooldown_and_blocks_new_process(monkeypatch) -> None:
    fake_redis = FakeRedis()
    calls: list[httpx.Request] = []
    monkeypatch.setattr(vinted_api, "ReliableVintedScraper", _forbidden_factory(calls))
    monkeypatch.setattr(vinted_api, "_get_redis", lambda: fake_redis)
    monkeypatch.setattr(vinted_api.VintedAPIConnector, "_cooldown_until", 0.0)

    with pytest.raises(RuntimeError, match="HTTP 403"):
        await vinted_api.VintedAPIConnector().search_items("test")
    assert len(calls) == 1
    assert await source_cooldown_active(fake_redis, "vinted")
    assert fake_redis.store[cooldown_key("vinted")][1] == vinted_api.COOLDOWN_SECONDS

    # A fresh worker process has no in-process state but still sees the Redis key.
    monkeypatch.setattr(vinted_api.VintedAPIConnector, "_cooldown_until", 0.0)
    with pytest.raises(RuntimeError, match="cooldown"):
        await vinted_api.VintedAPIConnector().search_items("test")
    assert len(calls) == 1


async def test_redis_outage_falls_back_to_in_process_cooldown(monkeypatch) -> None:
    calls: list[httpx.Request] = []
    monkeypatch.setattr(vinted_api, "ReliableVintedScraper", _forbidden_factory(calls))
    monkeypatch.setattr(vinted_api, "_get_redis", lambda: BrokenRedis())
    monkeypatch.setattr(vinted_api.VintedAPIConnector, "_cooldown_until", 0.0)

    with pytest.raises(RuntimeError, match="HTTP 403"):
        await vinted_api.VintedAPIConnector().search_items("test")
    with pytest.raises(RuntimeError, match="cooldown"):
        await vinted_api.VintedAPIConnector().search_items("test")
    assert len(calls) == 1


async def test_scheduler_skips_source_during_cooldown(monkeypatch):
    monkeypatch.setattr(worker, "_active_product_ids", lambda source: ["product"])
    pool = AsyncMock()
    pool.exists.return_value = 1

    result = await worker.scheduled_source_ingestion({"redis": pool}, "vinted")

    assert result == {"status": "cooldown", "source": "vinted"}
    pool.enqueue_job.assert_not_called()


async def test_scheduler_enqueues_when_no_cooldown(monkeypatch):
    monkeypatch.setattr(worker, "_active_product_ids", lambda source: ["product"])
    pool = AsyncMock()
    pool.exists.return_value = 0
    pool.enqueue_job.return_value = object()

    result = await worker.scheduled_source_ingestion({"redis": pool}, "vinted")

    assert result == {"product": "queued"}

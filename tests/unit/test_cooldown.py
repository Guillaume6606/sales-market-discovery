"""Shared (Redis-backed) source cooldown: connector sets it, scheduler honours it."""

import httpx
import pytest

from ingestion.connectors import vinted_api
from ingestion.connectors.vinted_transport import ReliableVintedScraper


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


def _forbidden_factory(calls: list[httpx.Request], status: int = 403):
    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status)

    def scraper_factory(baseurl, *, config):
        return ReliableVintedScraper(baseurl, config={"transport": httpx.MockTransport(respond)})

    return scraper_factory


@pytest.mark.parametrize("status", [401, 403, 429])
async def test_forbidden_sets_shared_cooldown_and_blocks_new_process(
    monkeypatch, status: int
) -> None:
    fake_redis = FakeRedis()
    calls: list[httpx.Request] = []
    monkeypatch.setattr(vinted_api, "ReliableVintedScraper", _forbidden_factory(calls, status))
    monkeypatch.setattr(vinted_api, "_get_redis", lambda: fake_redis)
    monkeypatch.setattr(vinted_api.VintedAPIConnector, "_cooldown_until", 0.0)

    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        await vinted_api.VintedAPIConnector().search_items("test")
    assert len(calls) == 1
    assert fake_redis.store["ingestion:cooldown:vinted"][1] == 300

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


async def test_expired_shared_cooldown_allows_new_attempt(monkeypatch) -> None:
    fake_redis = FakeRedis()
    calls: list[httpx.Request] = []
    monkeypatch.setattr(vinted_api, "ReliableVintedScraper", _forbidden_factory(calls))
    monkeypatch.setattr(vinted_api, "_get_redis", lambda: fake_redis)
    monkeypatch.setattr(vinted_api.VintedAPIConnector, "_cooldown_until", 0.0)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        await vinted_api.VintedAPIConnector().search_items("test")
    fake_redis.store.clear()
    monkeypatch.setattr(vinted_api.VintedAPIConnector, "_cooldown_until", 0.0)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        await vinted_api.VintedAPIConnector().search_items("test")
    assert len(calls) == 2

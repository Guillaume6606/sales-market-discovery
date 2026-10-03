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


@pytest.mark.parametrize("status", [401, 403, 429])
async def test_leboncoin_denial_sets_shared_cooldown(monkeypatch, status: int) -> None:
    from lbc.exceptions import DatadomeError, RequestError

    from ingestion.connectors import leboncoin_api

    redis = FakeRedis()
    calls = []

    class Client:
        def search(self, **kwargs):
            calls.append(kwargs)
            if status == 403:
                raise DatadomeError("Access blocked by Datadome")
            raise RequestError(f"Request failed with status code {status}.")

    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: redis, raising=False)
    monkeypatch.setattr(leboncoin_api.LeBonCoinAPIConnector, "_cooldown_until", 0.0, raising=False)
    connector = leboncoin_api.LeBonCoinAPIConnector(client=Client())
    with pytest.raises(RuntimeError):
        await connector.search_items(keyword="test")
    assert redis.store["ingestion:cooldown:leboncoin"][1] == 300
    monkeypatch.setattr(leboncoin_api.LeBonCoinAPIConnector, "_cooldown_until", 0.0)
    with pytest.raises(RuntimeError, match="cooldown"):
        await connector.search_items(keyword="test")
    assert len(calls) == 1


async def test_leboncoin_shared_cooldown_prevents_client_bootstrap(monkeypatch) -> None:
    from ingestion.connectors import leboncoin_api

    redis = FakeRedis()
    await redis.set("ingestion:cooldown:leboncoin", "blocked", ex=300)
    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: redis, raising=False)
    monkeypatch.setattr(leboncoin_api.LeBonCoinAPIConnector, "_cooldown_until", 0.0, raising=False)

    def forbidden_bootstrap(*args, **kwargs):
        raise AssertionError("No marketplace requests allowed during cooldown")

    monkeypatch.setattr(leboncoin_api.lbc, "Client", forbidden_bootstrap)
    with pytest.raises(RuntimeError, match="cooldown"):
        await leboncoin_api.fetch_leboncoin_api_listings("test")


async def test_leboncoin_redis_outage_retains_local_cooldown(monkeypatch) -> None:
    from lbc.exceptions import DatadomeError

    from ingestion.connectors import leboncoin_api

    calls = []

    class Client:
        def search(self, **kwargs):
            calls.append(kwargs)
            raise DatadomeError("denied")

    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: BrokenRedis(), raising=False)
    monkeypatch.setattr(leboncoin_api.LeBonCoinAPIConnector, "_cooldown_until", 0.0, raising=False)
    connector = leboncoin_api.LeBonCoinAPIConnector(client=Client())
    with pytest.raises(RuntimeError):
        await connector.search_items(keyword="test")
    with pytest.raises(RuntimeError, match="cooldown"):
        await connector.search_items(keyword="test")
    assert len(calls) == 1


async def test_leboncoin_non_denial_failure_does_not_start_cooldown(monkeypatch) -> None:
    from types import SimpleNamespace

    from lbc.exceptions import RequestError

    from ingestion.connectors import leboncoin_api

    redis = FakeRedis()
    calls = []

    class Client:
        def search(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RequestError("Request failed with status code 500.")
            return SimpleNamespace(ads=[])

    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: redis)
    connector = leboncoin_api.LeBonCoinAPIConnector(client=Client())
    with pytest.raises(RuntimeError):
        await connector.search_items(keyword="test")
    assert await connector.search_items(keyword="test") == []
    assert redis.store == {}
    assert len(calls) == 2


async def test_leboncoin_expired_cooldown_allows_next_attempt(monkeypatch) -> None:
    from lbc.exceptions import DatadomeError

    from ingestion.connectors import leboncoin_api

    redis = FakeRedis()
    calls = []

    class Client:
        def search(self, **kwargs):
            calls.append(kwargs)
            raise DatadomeError("denied")

    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: redis)
    connector = leboncoin_api.LeBonCoinAPIConnector(client=Client())
    with pytest.raises(RuntimeError, match="HTTP 403"):
        await connector.search_items(keyword="test")
    redis.store.clear()
    monkeypatch.setattr(leboncoin_api.LeBonCoinAPIConnector, "_cooldown_until", 0.0)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        await connector.search_items(keyword="test")
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 429])
@pytest.mark.parametrize("denial_at", ["detail", "profile"])
async def test_leboncoin_detail_denial_blocks_subsequent_request(
    monkeypatch, status: int, denial_at: str
) -> None:
    from lbc.exceptions import RequestError

    from ingestion.connectors import leboncoin_api

    redis = FakeRedis()
    calls = []

    class Ad:
        @property
        def user(self):
            calls.append("profile")
            raise RequestError(f"Request failed with status code {status}.")

    class Client:
        def get_ad(self, listing_id):
            calls.append("detail")
            if denial_at == "detail":
                raise RequestError(f"Request failed with status code {status}.")
            return Ad()

    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: redis)
    connector = leboncoin_api.LeBonCoinAPIConnector(client=Client())

    first = await connector.fetch_detail("123", 1)
    assert (first is None) == (denial_at == "detail")
    assert redis.store["ingestion:cooldown:leboncoin"][1] == 300
    monkeypatch.setattr(leboncoin_api.LeBonCoinAPIConnector, "_cooldown_until", 0.0)
    assert await connector.fetch_detail("123", 1) is None
    assert calls == (["detail"] if denial_at == "detail" else ["detail", "profile"])


async def test_leboncoin_pipeline_cooldown_skips_client_bootstrap(monkeypatch) -> None:
    from unittest.mock import MagicMock, Mock

    from ingestion import computation, ingestion
    from ingestion.connectors import leboncoin_api

    redis = FakeRedis()
    await redis.set("ingestion:cooldown:leboncoin", "blocked", ex=300)
    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: redis)
    bootstrap = Mock(side_effect=AssertionError("No requests allowed during cooldown"))
    monkeypatch.setattr(leboncoin_api.lbc, "Client", bootstrap)
    monkeypatch.setattr(ingestion.settings, "detail_fetch_enabled", True)
    db = MagicMock()
    db.get.return_value.is_active = True
    db.query.return_value.outerjoin.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
        object()
    ]
    session = MagicMock()
    session.return_value.__enter__.return_value = db
    monkeypatch.setattr(ingestion, "SessionLocal", session)
    after_detail = Mock(side_effect=RuntimeError("detail phase completed"))
    monkeypatch.setattr(computation, "compute_pmn_for_product", after_detail)

    with pytest.raises(RuntimeError, match="detail phase completed"):
        await ingestion.finish_product_pipeline("product", ["leboncoin"])
    after_detail.assert_called_once()
    bootstrap.assert_not_called()


async def test_leboncoin_detail_wrapper_skips_bootstrap_during_cooldown(monkeypatch) -> None:
    from unittest.mock import Mock

    from ingestion.connectors import leboncoin_api
    from ingestion.connectors.leboncoin import LeBonCoinConnector

    redis = FakeRedis()
    await redis.set("ingestion:cooldown:leboncoin", "blocked", ex=300)
    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: redis)
    bootstrap = Mock(side_effect=AssertionError("No requests allowed during cooldown"))
    monkeypatch.setattr(leboncoin_api.lbc, "Client", bootstrap)
    assert await LeBonCoinConnector().fetch_detail("123", 1) is None
    bootstrap.assert_not_called()


async def test_leboncoin_detail_wrapper_returns_payload_to_orchestrator(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from ingestion import detail_fetch
    from ingestion.connectors import leboncoin_api
    from ingestion.connectors.leboncoin import LeBonCoinConnector
    from libs.common.models import ListingDetail

    client = SimpleNamespace(get_ad=lambda _: SimpleNamespace(user=None))
    monkeypatch.setattr(leboncoin_api.lbc, "Client", lambda **kwargs: client)
    monkeypatch.setattr(detail_fetch.settings, "detail_fetch_enabled", True)
    monkeypatch.setitem(detail_fetch.RATE_LIMITS, "leboncoin", 0)

    def persist(db, detail):
        assert isinstance(detail, ListingDetail)
        assert detail.obs_id == 1
        return True

    monkeypatch.setattr(detail_fetch, "persist_listing_detail", persist)
    result = await detail_fetch.fetch_and_persist_details(
        Mock(),
        [SimpleNamespace(listing_id="123", obs_id=1, price=100)],
        "leboncoin",
        None,
        None,
        None,
        LeBonCoinConnector().fetch_detail,
    )
    assert result == 1

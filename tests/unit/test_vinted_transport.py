import httpx
import pytest

from ingestion.connectors.vinted_transport import ReliableVintedScraper


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [(403, 1), (401, 1), (429, 1), (400, 1), (503, 3)])
async def test_bootstrap_bounds_http_failures(status: int, expected: int) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status)

    async with ReliableVintedScraper(
        "https://www.vinted.fr", config={"transport": httpx.MockTransport(respond)}
    ) as scraper:
        scraper.RETRY_DELAY = 0
        with pytest.raises(httpx.HTTPStatusError):
            await scraper.refresh_cookie()
    assert len(calls) == expected
    assert scraper._client.is_closed


@pytest.mark.asyncio
async def test_search_recovers_transient_failure_and_parses_items() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"items": [{"id": 123, "title": "test"}]})

    async with ReliableVintedScraper(
        "https://www.vinted.fr",
        session_cookie={"access_token_web": "test"},
        config={"transport": httpx.MockTransport(respond)},
    ) as scraper:
        scraper.RETRY_DELAY = 0
        result = await scraper.search({"search_text": "test"})
    assert result[0].id == 123
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_api_unauthorized_does_not_recurse() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401)

    async with ReliableVintedScraper(
        "https://www.vinted.fr",
        session_cookie={"access_token_web": "test"},
        config={"transport": httpx.MockTransport(respond)},
    ) as scraper:
        with pytest.raises(httpx.HTTPStatusError):
            await scraper.item("123")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_connector_proxy_cleanup_and_forbidden_cooldown(monkeypatch) -> None:
    from ingestion.connectors import vinted_api

    calls = []
    clients = []
    real_scraper = ReliableVintedScraper

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403)

    def scraper_factory(baseurl, *, config):
        assert config["proxy"] == "http://user:secret@proxy.test:8000"
        scraper = real_scraper(baseurl, config={"transport": httpx.MockTransport(respond)})
        clients.append(scraper._client)
        return scraper

    monkeypatch.setattr(
        vinted_api.settings, "scraping_proxy_url", "http://user:secret@proxy.test:8000"
    )
    monkeypatch.setattr(vinted_api, "ReliableVintedScraper", scraper_factory)
    monkeypatch.setattr(vinted_api.VintedAPIConnector, "_cooldown_until", 0.0)
    connector = vinted_api.VintedAPIConnector()
    with pytest.raises(RuntimeError, match="HTTP 403") as failure:
        await connector.search_items("test")
    assert "secret" not in str(failure.value)
    assert failure.value.__suppress_context__
    assert await vinted_api.VintedAPIConnector().fetch_detail("123", 1) is None
    assert len(calls) == 1
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_transport_failure_has_bounded_attempts() -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectTimeout("connection timed out", request=request)

    async with ReliableVintedScraper(
        "https://www.vinted.fr", config={"transport": httpx.MockTransport(respond)}
    ) as scraper:
        scraper.RETRY_DELAY = 0
        with pytest.raises(httpx.ConnectTimeout):
            await scraper.refresh_cookie()
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_french_bootstrap_uses_french_language_and_extracts_cookie() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept-Language"] == "fr-FR,fr;q=0.9"
        return httpx.Response(200, headers={"Set-Cookie": "access_token_web=test; Path=/"})

    async with ReliableVintedScraper(
        "https://www.vinted.fr", config={"transport": httpx.MockTransport(respond)}
    ) as scraper:
        assert await scraper.refresh_cookie() == {"access_token_web": "test"}

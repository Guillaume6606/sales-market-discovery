"""Bounded HTTP requests for the Vinted parser's async client."""

import asyncio
from typing import Any

import httpx
from vinted_scraper import AsyncVintedScraper
from vinted_scraper.utils import (
    extract_cookie_from_response,
    get_cookie_headers,
    get_curl_headers,
)


class ReliableVintedScraper(AsyncVintedScraper):
    """Avoid upstream's repeated forbidden bootstrap and recursive 401 retries."""

    RETRY_DELAY = 1.0

    async def _get(self, endpoint: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self._client.get(endpoint, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 2:
                    raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {500, 502, 503, 504} or attempt == 2:
                    raise
            await asyncio.sleep(self.RETRY_DELAY * (2**attempt))
        raise RuntimeError("Vinted request exhausted retries")

    async def refresh_cookie(self, retries: int = 1) -> dict[str, str]:
        headers = get_cookie_headers(self.baseurl, self.user_agent)
        headers["Accept-Language"] = "fr-FR,fr;q=0.9"
        response = await self._get("/", headers=headers)
        cookies = extract_cookie_from_response(response, self.cookie_names)
        if not cookies:
            raise RuntimeError("Vinted bootstrap returned no session cookie")
        return cookies

    async def curl(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._get(
            endpoint,
            headers=get_curl_headers(self.baseurl, self.user_agent, self.session_cookie),
            params=params,
        )
        return response.json()

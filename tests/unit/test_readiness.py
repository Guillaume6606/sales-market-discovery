from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.routers.feedback import _verify_webhook_secret
from libs.common.settings import settings


@pytest.mark.asyncio
async def test_readiness_requires_a_live_worker(monkeypatch):
    from backend import main

    assert hasattr(main, "readiness")
    redis = AsyncMock()
    redis.get.return_value = None
    monkeypatch.setattr(main, "arq_pool", redis)
    with pytest.raises(HTTPException) as error:
        await main.readiness(MagicMock())
    assert error.value.status_code == 503


def test_production_webhook_cannot_disable_authentication(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "telegram_webhook_secret", None)
    with pytest.raises(HTTPException) as error:
        _verify_webhook_secret(Request({"type": "http", "headers": []}))
    assert error.value.status_code == 503

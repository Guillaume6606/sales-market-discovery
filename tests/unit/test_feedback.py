import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.routers.feedback import _verify_webhook_secret
from libs.common.settings import settings


@pytest.mark.parametrize("app_env", ["local", "test", "production"])
def test_webhook_requires_secret_in_every_environment(monkeypatch, app_env: str) -> None:
    monkeypatch.setattr(settings, "app_env", app_env)
    monkeypatch.setattr(settings, "telegram_webhook_secret", None)
    with pytest.raises(HTTPException) as error:
        _verify_webhook_secret(Request({"type": "http", "headers": []}))
    assert error.value.status_code == 503


@pytest.mark.parametrize("token", [None, "incorrect"])
def test_webhook_rejects_missing_or_incorrect_secret(monkeypatch, token: str | None) -> None:
    monkeypatch.setattr(settings, "telegram_webhook_secret", "configured-secret")
    headers = [] if token is None else [(b"x-telegram-bot-api-secret-token", token.encode())]
    with pytest.raises(HTTPException) as error:
        _verify_webhook_secret(Request({"type": "http", "headers": headers}))
    assert error.value.status_code == 401


def test_webhook_accepts_matching_secret(monkeypatch) -> None:
    monkeypatch.setattr(settings, "telegram_webhook_secret", "configured-secret")
    _verify_webhook_secret(
        Request(
            {
                "type": "http",
                "headers": [(b"x-telegram-bot-api-secret-token", b"configured-secret")],
            }
        )
    )

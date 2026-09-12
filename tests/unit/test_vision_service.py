import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ingestion.listing_images import ImageInput
from libs.common import vision_service as module


class Store:
    def __init__(self):
        self.claims = {}
        self.attempts = []
        self.remaining = 10

    def claim(self, key):
        if key in self.claims:
            return False, self.claims[key]
        self.claims[key] = None
        return "test-owner", None

    def reserve(self, *args):
        if self.remaining <= 0:
            return None
        self.remaining -= 1
        return self.remaining + 1

    def record_attempt(self, *args):
        self.attempts.append(args)

    def finish(self, key, owner, result):
        self.claims[key] = result

    def release_unpaid(self, key):
        if not self.attempts:
            del self.claims[key]


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(module.settings, "vision_enabled", True)
    monkeypatch.setattr(module.settings, "gemini_api_key", "fake-test-key")
    monkeypatch.setattr(module.settings, "vision_provider", "gemini")
    monkeypatch.setattr(module.settings, "vision_model", "gemini-2.5-flash-lite")
    store = Store()
    with (
        patch.object(module, "VisionStore", return_value=store),
        patch.object(module, "prepare_images", new=AsyncMock(return_value=[])),
    ):
        yield store


def output():
    return json.dumps(
        dict(
            item_class="accessory",
            model=None,
            variant=None,
            included_accessories=[],
            seller_condition_claims=[],
            visible_defects=[],
            contradictions=[],
            unknown_fields=["model"],
            evidence=[],
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model", ["gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]
)
async def test_google_uses_json_schema_field_for_strict_objects(monkeypatch, model):
    monkeypatch.setattr(module.settings, "vision_model", model)
    monkeypatch.setattr(module.settings, "vision_provider", "gemini")
    monkeypatch.setattr(module.settings, "gemini_api_key", "fake-test-key")
    generate = AsyncMock(return_value=SimpleNamespace(text=output(), usage_metadata=None))
    with patch.object(module.genai, "Client") as client_type:
        client = client_type.return_value
        client.aio.models.generate_content = generate
        client.aio.aclose = AsyncMock()
        await module._call_provider("test", [])
    config = generate.call_args.kwargs["config"]
    assert config.response_schema is None
    assert config.response_json_schema["additionalProperties"] is False
    assert config.response_json_schema["$defs"]["Evidence"]["additionalProperties"] is False
    assert len(config.response_json_schema["$defs"]["Evidence"]["anyOf"]) == 2
    if model.startswith("gemini-3."):
        assert config.thinking_config.thinking_level == module.types.ThinkingLevel.MINIMAL
    else:
        assert config.thinking_config.thinking_budget == 0


@pytest.mark.asyncio
async def test_cache_and_unknown_usage(configured):
    with patch.object(module, "_call_provider", new=AsyncMock(return_value=(output(), {}))) as call:
        first = await module.extract_listing("PS5 controller", "controller only", [])
        second = await module.extract_listing("PS5 controller", "controller only", [])
    assert first.status == "text_only"
    assert second.cache_hit
    assert call.await_count == 1
    assert configured.attempts[0][2] is None


def test_fingerprint_changes_with_model_and_content(monkeypatch):
    a = module.request_key("a", "b", [], None)
    assert a != module.request_key("a", "c", [], None)
    assert a != module.request_key("a", "b", [ImageInput(b"a", "image/jpeg", "hash")], None)
    monkeypatch.setattr(module.settings, "vision_model", "other")
    assert a != module.request_key("a", "b", [], None)


@pytest.mark.asyncio
async def test_budget_exhaustion_does_not_call_provider(configured):
    configured.remaining = 0
    with patch.object(module, "_call_provider", new=AsyncMock()) as call:
        assert (await module.extract_listing("a", "b", [])).status == "budget_exhausted"
        call.assert_not_called()
    assert next(iter(configured.claims.values()))["status"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_invalid_json_is_explicit_error(configured):
    with patch.object(module, "_call_provider", new=AsyncMock(return_value=("invalid", {}))):
        assert (await module.extract_listing("a", "b", [])).status == "error"


@pytest.mark.asyncio
async def test_retry_is_separately_reserved(configured):
    from google.genai.errors import ClientError

    with patch.object(
        module,
        "_call_provider",
        new=AsyncMock(
            side_effect=[
                ClientError(429, {"error": {"message": "rate limited"}}),
                (output(), {"input_tokens": 10, "output_tokens": 20}),
            ]
        ),
    ):
        assert (await module.extract_listing("a", "b", [])).status == "text_only"
    assert len(configured.attempts) == 2
    assert configured.attempts[0][2] is None
    assert configured.attempts[1][2] == Decimal("0.000009")


@pytest.mark.asyncio
async def test_exception_after_claim_finishes_owned_request(configured):
    with patch.object(configured, "reserve", side_effect=RuntimeError("database unavailable")):
        result = await module.extract_listing("a", "b", [])
    assert result.status == "error"
    assert next(iter(configured.claims.values()))["status"] == "error"


@pytest.mark.asyncio
async def test_cancelled_call_finishes_claim_but_retains_reservation(configured):
    import asyncio

    with patch.object(module, "_call_provider", new=AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await module.extract_listing("a", "b", [])
    assert configured.remaining == 9
    assert configured.attempts == []
    assert next(iter(configured.claims.values()))["status"] == "error"

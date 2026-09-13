from argparse import Namespace
from decimal import Decimal

import pytest

from libs.common import vision_service as vision
from scripts.benchmark_listing_vision import FileBudget, configured
from scripts.evaluate_listing_vision import input_fingerprint


def test_unknown_failed_cost_survives_resume_and_blocks_overspending(tmp_path):
    path = tmp_path / "budget.json"
    budget = FileBudget(path, {"EUR": Decimal(1)})
    attempt, cached = budget.claim("one", "EUR", Decimal(".6"))
    assert cached is None
    budget.finish(attempt, {"status": "error", "cost": None}, None)
    resumed = FileBudget(path, {"EUR": Decimal(1)})
    assert resumed.claim("two", "EUR", Decimal(".6"))[1]["status"] == "budget_exhausted"
    assert resumed.claim("one", "EUR", Decimal(".6"))[1]["cache_hit"]
    assert (
        resumed.claim("one", "EUR", Decimal(".6"), retry_errors=True)[1]["status"]
        == "budget_exhausted"
    )


def test_success_reconciles_and_cache_does_not_respend(tmp_path):
    budget = FileBudget(tmp_path / "budget.json", {"USD": Decimal(1)})
    attempt, _ = budget.claim("one", "USD", Decimal(".8"))
    budget.finish(attempt, {"status": "completed", "cost": ".1"}, Decimal(".1"))
    assert budget.claim("one", "USD", Decimal(".8"))[1]["cache_hit"]
    assert budget.claim("two", "USD", Decimal(".8"))[0] is not None
    with pytest.raises(ValueError, match="already finalized"):
        budget.finish(attempt, {"status": "completed"}, Decimal(0))


def test_crashed_attempt_is_pending_and_reservation_retained(tmp_path):
    budget = FileBudget(tmp_path / "budget.json", {"EUR": Decimal(1)})
    budget.claim("one", "EUR", Decimal(".8"))
    assert budget.claim("one", "EUR", Decimal(".8"), retry_errors=True)[1]["status"] == "pending"
    assert budget.claim("two", "EUR", Decimal(".8"))[1]["status"] == "budget_exhausted"


def test_settings_restore_after_error_and_unknown_price_fails_closed():
    previous = vision.settings.vision_model
    args = Namespace(
        provider="scaleway",
        model="unknown-priced-model",
        mode="json_object",
        max_tokens=512,
        local_digest="",
    )
    with pytest.raises(ValueError, match="price schedule"), configured(args):
        vision._price()
    assert vision.settings.vision_model == previous


def test_reference_fingerprint_includes_target_metadata():
    row = {
        "title": "Console",
        "target": "PS5",
        "target_metadata": {"name": "PS5", "capacity": "1TB"},
    }
    changed = {**row, "target_metadata": {"name": "PS5", "capacity": "2TB"}}
    assert input_fingerprint(row) != input_fingerprint(changed)


@pytest.mark.parametrize("amount", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_ledger_rejects_invalid_reservations_and_caps(tmp_path, amount):
    with pytest.raises(ValueError):
        FileBudget(tmp_path / "invalid.json", {"USD": amount})
    budget = FileBudget(tmp_path / "budget.json", {"USD": Decimal(1)})
    with pytest.raises(ValueError):
        budget.claim("invalid", "USD", amount)


@pytest.mark.asyncio
async def test_benchmark_rejects_visual_claims_without_images(monkeypatch):
    import json
    from unittest.mock import AsyncMock

    from scripts.benchmark_listing_vision import run_case

    raw = json.dumps(
        {
            "item_class": "exact_device",
            "model": "PS5",
            "variant": None,
            "included_accessories": [],
            "seller_reported_faults": [],
            "visible_damage": [],
            "text_photo_conflict": False,
        }
    )
    monkeypatch.setattr(
        vision,
        "_call_provider",
        AsyncMock(return_value=(raw, {"input_tokens": 10, "output_tokens": 10})),
    )
    args = Namespace(
        provider="gemini", model="test", local_digest="", mode="json_object", max_tokens=512
    )
    row = {"id": "1", "group_id": "g", "split": "dev"}
    record = await run_case(
        None, args, row, [], "prompt", "key", "USD", Decimal(1), Decimal(1), None
    )
    assert record["status"] == "error"
    assert record["cost"] == "0.00002"
    assert record["raw"] == raw


@pytest.mark.asyncio
async def test_oversized_input_is_recorded_without_reservation_and_next_row_runs(
    tmp_path, monkeypatch
):
    import json
    from unittest.mock import AsyncMock

    from scripts import benchmark_listing_vision as module

    rows = [
        {
            "id": "long",
            "group_id": "a",
            "split": "dev",
            "title": "a",
            "target": "PS5",
            "description": "x" * 32001,
            "image_hashes": [],
        },
        {
            "id": "short",
            "group_id": "b",
            "split": "dev",
            "title": "b",
            "target": "PS5",
            "description": "",
            "image_hashes": [],
        },
    ]
    monkeypatch.setattr(module, "read_manifest", lambda path: rows)
    images = tmp_path / "images"
    images.mkdir()
    (images / "index.json").write_text(json.dumps({r["id"]: {"hashes": []} for r in rows}))
    args = Namespace(
        manifest=tmp_path / "manifest",
        split="dev",
        limit=2,
        images=images,
        provider="gemini",
        model="gemini-3.1-flash-lite",
        mode="json_object",
        max_tokens=512,
        local_digest="",
        dry_run=False,
        ledger=tmp_path / "ledger.json",
        output=tmp_path / "results.jsonl",
        usd_cap=Decimal(5),
        eur_cap=Decimal(10),
        text_only=False,
        retry_errors=False,
    )
    call = AsyncMock(return_value={"id": "short", "status": "completed", "cost": "0.01"})
    monkeypatch.setattr(module, "run_case", call)
    await module.run(args)
    records = [json.loads(line) for line in args.output.read_text().splitlines()]
    assert [r["status"] for r in records] == ["input_error", "completed"]
    assert records[0]["id"] == "long" and records[0]["paid_attempt"] is False
    assert len(json.loads(args.ledger.read_text())["attempts"]) == 1
    assert call.await_count == 1

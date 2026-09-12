import json

import pytest

from scripts.evaluate_listing_vision import read_manifest, summarize


def test_duplicate_images_cannot_cross_holdout(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    rows = [
        dict(id=str(i), group_id=str(i), split=split, photo_urls=["https://same/photo"])
        for i, split in enumerate(["dev", "holdout"])
    ]
    manifest.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="crosses"):
        read_manifest(manifest)


def test_unlabeled_predictions_do_not_become_accuracy():
    report = summarize(
        [
            {
                "result": {"status": "completed", "extraction": {"item_class": "exact_device"}},
                "expected": None,
            }
        ]
    )
    assert report["quality_status"] == "not_measured_no_human_labels"
    assert report["class_accuracy"] is None


def test_abstention_counts_in_recall_and_unknown_cost_stays_unknown():
    rows = [
        {
            "expected": {"item_class": "exact_device", "model": "PS5"},
            "heuristic_class": "exact_device",
            "result": {"status": "error"},
        },
        {
            "expected": {"item_class": "accessory", "model": None},
            "heuristic_class": "accessory",
            "result": {
                "status": "completed",
                "extraction": {"item_class": "exact_device", "model": "PS5"},
            },
        },
    ]
    report = summarize(rows)
    assert report["accessory_false_acceptance"] == 1
    assert report["fields"]["model"]["recall"] == 0
    assert report["class_accuracy_delta"] == -1
    assert report["unknown_cost_calls"] == 2


@pytest.mark.asyncio
async def test_photos_are_frozen_and_tampering_rejected(tmp_path, monkeypatch):
    import hashlib
    from unittest.mock import AsyncMock

    from ingestion.listing_images import ImageInput
    from scripts import evaluate_listing_vision as module

    image = ImageInput(b"frozen", "image/jpeg", hashlib.sha256(b"frozen").hexdigest())
    downloader = AsyncMock(return_value=[image])
    monkeypatch.setattr(module, "prepare_images", downloader)
    rows = [{"id": "one", "split": "dev", "photo_urls": ["https://photo"]}]
    manifest = tmp_path / "manifest.jsonl"
    first = await module.frozen_images(manifest, rows)
    second = await module.frozen_images(manifest, rows)
    assert first == second
    assert downloader.await_count == 1
    (tmp_path / "manifest-images" / (image.digest + ".jpg")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="content changed"):
        await module.frozen_images(manifest, rows)


@pytest.mark.asyncio
async def test_actual_image_hashes_check_cross_split_duplicates(tmp_path, monkeypatch):
    import hashlib
    from unittest.mock import AsyncMock

    from ingestion.listing_images import ImageInput
    from scripts import evaluate_listing_vision as module

    image = ImageInput(b"same", "image/jpeg", hashlib.sha256(b"same").hexdigest())
    monkeypatch.setattr(module, "prepare_images", AsyncMock(return_value=[image]))
    rows = [
        {"id": str(i), "split": split, "photo_urls": [f"https://different/{i}"]}
        for i, split in enumerate(["dev", "holdout"])
    ]
    with pytest.raises(ValueError, match="Actual duplicate"):
        await module.frozen_images(tmp_path / "m.jsonl", rows)

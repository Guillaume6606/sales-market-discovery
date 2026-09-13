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


def test_selection_split_and_frozen_input_hash(tmp_path):
    from scripts.evaluate_listing_vision import input_fingerprint

    row = {"id": "one", "group_id": "one", "split": "selection", "title": "Camera"}
    row["input_hash"] = input_fingerprint(row)
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(row))
    assert read_manifest(path) == [row]
    row["title"] = "Changed"
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="input hash"):
        read_manifest(path)


def test_null_references_are_unscored_and_empty_lists_are_scored():
    row = {
        "expected": {"model": None, "visible_damage": [], "text_photo_conflict": False},
        "result": {
            "status": "completed",
            "extraction": {
                "model": "invented",
                "visible_damage": [],
                "text_photo_conflict": False,
            },
        },
    }
    fields = summarize([row])["fields"]
    assert fields["model"]["scored_rows"] == 0
    assert fields["model"]["precision"] is None
    assert fields["visible_damage"]["exact_match"] == 1
    assert fields["visible_damage"]["precision"] is None
    assert fields["text_photo_conflict"]["exact_match"] == 1


def test_failed_output_is_not_a_correct_uncertain_prediction_or_empty_list():
    report = summarize(
        [
            {
                "expected": {"item_class": "uncertain", "visible_damage": []},
                "result": {
                    "status": "validation_error",
                    "extraction": {
                        "item_class": "uncertain",
                        "visible_damage": [],
                    },
                },
            }
        ]
    )
    assert report["class_accuracy"] == 0
    assert report["validated_response_rate"] == 0
    assert report["coverage"] == 0
    assert report["fields"]["visible_damage"]["exact_match"] == 0
    assert report["fields"]["visible_damage"]["conditional_exact_match"] is None


def test_canonical_matching_is_conservative():
    from scripts.evaluate_listing_vision import canonical

    assert canonical("  USB   CABLE ") == canonical("usb cable")
    assert canonical(["Case", "USB cable"]) == canonical(["usb cable", "case"])
    assert canonical("PS5") != canonical("PlayStation 5")
    assert canonical("3 batteries") != canonical("batteries")


def test_paired_bootstrap_uses_groups_and_is_reproducible():
    from scripts.evaluate_listing_vision import paired_group_bootstrap

    rows = [
        {
            "id": str(index),
            "group_id": str(index // 2),
            "expected": {"item_class": "exact_device"},
            "heuristic_class": "accessory",
            "result": {"status": "completed", "extraction": {"item_class": "exact_device"}},
        }
        for index in range(4)
    ]
    result = paired_group_bootstrap(rows)
    assert result == paired_group_bootstrap(rows)
    assert result["groups"] == 2
    assert result["rows"] == 4
    assert result["delta"] == 1
    assert result["ci95"] == [1, 1]
    assert result["resamples"] == 2000
    baseline = [{**row, "title": "Changed"} for row in rows]
    with pytest.raises(ValueError, match="identical IDs and inputs"):
        paired_group_bootstrap(rows, baseline)


def test_failed_attempts_remain_in_safety_denominator():
    report = summarize(
        [
            {
                "expected": {"item_class": "wrong_variant"},
                "result": {"status": "error"},
            },
            {
                "expected": {"item_class": "wrong_variant"},
                "result": {"status": "completed", "extraction": {"item_class": "exact_device"}},
            },
        ]
    )
    assert report["unsafe_false_acceptance"]["wrong_variant"] == {
        "rate": 0.5,
        "denominator": 2,
        "accepted": 1,
    }
    assert report["validated_response_rate"] == 0.5

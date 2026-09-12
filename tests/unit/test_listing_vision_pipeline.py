from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ingestion.listing_vision import input_fingerprint, vision_review_reasons
from libs.common.settings import settings


@pytest.fixture
def vision_listing(monkeypatch):
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_shadow_mode", False)
    obs = SimpleNamespace(title="PS5", condition="good", vision_result=None)
    detail = SimpleNamespace(description="Console avec lecteur", photo_urls=["https://img.test/a"])
    product = SimpleNamespace(name="PlayStation 5", search_query="PS5")
    obs.vision_result = {
        "pipeline": "vision",
        "status": "completed",
        "input_fingerprint": input_fingerprint(obs, detail, product),
        "checked_at": datetime.now(UTC).isoformat(),
        "extraction": {
            "item_class": "exact_device",
            "model": "PS5",
            "variant": "standard disc edition",
            "contradictions": [],
        },
    }
    return obs, detail, product


def test_completed_vision_does_not_override_other_gates(vision_listing):
    assert vision_review_reasons(*vision_listing) == []


@pytest.mark.parametrize("status", ["pending", "error", "budget_exhausted", "text_only"])
def test_incomplete_vision_cannot_promote(vision_listing, status):
    vision_listing[0].vision_result["status"] = status
    assert vision_review_reasons(*vision_listing) == [f"vision_{status}"]


@pytest.mark.parametrize(
    "item_class", ["accessory", "parts_broken", "wrong_variant", "uncertain", "device_bundle"]
)
def test_noise_and_bundles_need_review(vision_listing, item_class):
    vision_listing[0].vision_result["extraction"]["item_class"] = item_class
    assert vision_review_reasons(*vision_listing)


def test_changed_description_invalidates_vision(vision_listing):
    vision_listing[1].description = "Boite vide uniquement"
    assert vision_review_reasons(*vision_listing) == ["vision_stale"]


def test_contradictory_photos_block_alerts(vision_listing):
    vision_listing[0].vision_result["extraction"]["contradictions"] = ["Photo PS4"]
    assert vision_review_reasons(*vision_listing) == ["vision_contradictions"]


def test_unknown_variant_blocks_alerts(vision_listing):
    vision_listing[0].vision_result["extraction"]["unknown_fields"] = ["variant"]
    assert vision_review_reasons(*vision_listing) == ["vision_identity_uncertain"]


def test_null_variant_without_unknown_flag_blocks_alerts(vision_listing):
    vision_listing[0].vision_result["extraction"]["variant"] = None
    assert vision_review_reasons(*vision_listing) == ["vision_variant_unknown"]


def test_shadow_mode_preserves_existing_eligibility(vision_listing, monkeypatch):
    monkeypatch.setattr(settings, "vision_shadow_mode", True)
    vision_listing[0].vision_result = None
    assert vision_review_reasons(*vision_listing) == []

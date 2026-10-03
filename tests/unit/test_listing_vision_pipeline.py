from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ingestion.listing_vision import (
    input_fingerprint,
    run_listing_vision_batch,
    vision_review_reasons,
)
from libs.common.settings import settings
from libs.common.vision_schema import SCHEMA_VERSION, VisionResult


@pytest.fixture
def vision_listing(monkeypatch):
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_shadow_mode", False)
    obs = SimpleNamespace(title="PS5", condition="good", vision_result=None)
    detail = SimpleNamespace(
        description="Console avec lecteur",
        photo_urls=["https://img.test/a"],
        fetched_at=datetime.now(UTC),
    )
    product = SimpleNamespace(name="PlayStation 5", search_query="PS5")
    obs.vision_result = {
        "pipeline": "vision",
        "status": "completed",
        "schema_version": SCHEMA_VERSION,
        "input_fingerprint": input_fingerprint(obs, detail, product),
        "checked_at": datetime.now(UTC).isoformat(),
        "extraction": {
            "item_class": "exact_device",
            "model": "PS5",
            "variant": None,
            "included_accessories": [],
            "seller_reported_faults": [],
            "visible_damage": [],
            "text_photo_conflict": False,
        },
    }
    return obs, detail, product


def test_completed_vision_does_not_override_other_gates(vision_listing):
    assert vision_review_reasons(*vision_listing) == []


@pytest.mark.parametrize("detail_state", ["expired", "unknown", "before_summary_update"])
def test_stale_details_cannot_clear_a_recent_vision_result(vision_listing, detail_state):
    obs, detail, product = vision_listing
    if detail_state == "expired":
        detail.fetched_at = datetime.now(UTC) - timedelta(
            minutes=settings.alert_freshness_minutes + 1
        )
    elif detail_state == "unknown":
        detail.fetched_at = None
    else:
        obs.updated_at = detail.fetched_at + timedelta(seconds=1)
    assert vision_review_reasons(obs, detail, product) == ["vision_stale"]


def test_detail_refetch_requires_rechecking_cached_vision(vision_listing):
    obs, detail, product = vision_listing
    obs.vision_result["checked_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    detail.fetched_at = datetime.now(UTC)
    assert vision_review_reasons(obs, detail, product) == ["vision_stale"]


@pytest.mark.parametrize("shadow", [False, True])
async def test_expired_details_are_not_reanalysed_or_promoted(vision_listing, monkeypatch, shadow):
    obs, detail, product = vision_listing
    monkeypatch.setattr(settings, "vision_shadow_mode", shadow)
    detail.fetched_at = datetime.now(UTC) - timedelta(minutes=settings.alert_freshness_minutes + 1)
    obs.vision_result["checked_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    db = MagicMock()
    db.query.return_value.join.return_value.outerjoin.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
        (obs, detail, product)
    ]
    session = MagicMock()
    session.return_value.__enter__.return_value = db
    monkeypatch.setattr("ingestion.listing_vision.SessionLocal", session)
    extract = AsyncMock(return_value=VisionResult(status="completed"))
    monkeypatch.setattr("libs.common.vision_service.extract_listing", extract)
    summary = await run_listing_vision_batch()
    assert summary["status"] == "partial"
    assert summary["pending"] == 1
    assert obs.vision_result["status"] == "pending"
    assert obs.vision_result["error"] == "detail_stale"
    extract.assert_not_awaited()
    assert vision_review_reasons(obs, detail, product) == ([] if shadow else ["vision_stale"])


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


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("text_photo_conflict", True, "vision_text_photo_conflict"),
        ("text_photo_conflict", None, "vision_conflict_unknown"),
        ("visible_damage", None, "vision_photos_unassessed"),
        ("visible_damage", ["cracked screen"], "vision_visible_damage_review_required"),
        ("seller_reported_faults", None, "vision_seller_faults_unknown"),
        ("seller_reported_faults", ["does not turn on"], "vision_seller_faults_review_required"),
        ("model", None, "vision_model_unknown"),
    ],
)
def test_unsafe_or_unassessed_fields_block(vision_listing, field, value, reason):
    vision_listing[0].vision_result["extraction"][field] = value
    assert vision_review_reasons(*vision_listing) == [reason]


def test_variant_not_required_for_generic_target(vision_listing):
    vision_listing[0].vision_result["extraction"]["variant"] = None
    assert vision_review_reasons(*vision_listing) == []


@pytest.mark.parametrize("variant", [None, "black", "256GB"])
def test_required_capacity_cannot_be_replaced_by_colour(vision_listing, variant):
    obs, detail, product = vision_listing
    product.name = "iPhone 14 Pro 128GB"
    product.search_query = "iPhone 14 Pro"
    obs.vision_result["input_fingerprint"] = input_fingerprint(obs, detail, product)
    obs.vision_result["extraction"].update(model="iPhone 14 Pro", variant=variant)
    assert vision_review_reasons(*vision_listing) == ["vision_identity_uncertain"]


def test_capacity_in_model_satisfies_required_identity(vision_listing):
    obs, detail, product = vision_listing
    product.name = "iPhone 14 Pro 128GB"
    product.search_query = "iPhone 14 Pro"
    obs.vision_result["input_fingerprint"] = input_fingerprint(obs, detail, product)
    obs.vision_result["extraction"].update(model="iPhone 14 Pro 128GB", variant=None)
    assert vision_review_reasons(*vision_listing) == []


def test_historical_version_cannot_clear_listing(vision_listing):
    vision_listing[0].vision_result["schema_version"] = "listing-vision-v2"
    assert vision_review_reasons(*vision_listing) == ["vision_stale"]


def test_missing_v3_fields_are_not_optimistically_defaulted(vision_listing):
    del vision_listing[0].vision_result["extraction"]["visible_damage"]
    assert vision_review_reasons(*vision_listing) == ["vision_invalid_extraction"]


def test_shadow_mode_preserves_existing_eligibility(vision_listing, monkeypatch):
    monkeypatch.setattr(settings, "vision_shadow_mode", True)
    vision_listing[0].vision_result = None
    assert vision_review_reasons(*vision_listing) == []


@pytest.mark.parametrize(
    "description",
    [
        "Montre pas authentique.",
        "Réplique de montre.",
        "Cette montre est une contrefaçon.",
        "Replica watch",
        "Produit non authentique",
    ],
)
def test_seller_explicit_non_authentic_cannot_be_cleared(vision_listing, description):
    obs, detail, product = vision_listing
    detail.description = description
    obs.vision_result["input_fingerprint"] = input_fingerprint(obs, detail, product)
    assert vision_review_reasons(*vision_listing) == ["seller_declared_non_authentic"]


@pytest.mark.parametrize(
    "description",
    [
        "Ce n'est pas une contrefaçon.",
        "Ce n'est pas une réplique.",
        "Aucune contrefaçon.",
    ],
)
def test_negated_counterfeit_claim_is_not_positive_flag(vision_listing, description):
    obs, detail, product = vision_listing
    detail.description = description
    obs.vision_result["input_fingerprint"] = input_fingerprint(obs, detail, product)
    assert vision_review_reasons(*vision_listing) == []


@pytest.mark.parametrize("enabled,shadow", [(False, True), (True, True), (True, False)])
def test_seller_counterfeit_guard_preserves_shadow_behavior(
    vision_listing, monkeypatch, enabled, shadow
):
    monkeypatch.setattr(settings, "vision_enabled", enabled)
    monkeypatch.setattr(settings, "vision_shadow_mode", shadow)
    vision_listing[1].description = "Montre pas authentique"
    expected = ["seller_declared_non_authentic"] if enabled and not shadow else []
    assert vision_review_reasons(*vision_listing) == expected

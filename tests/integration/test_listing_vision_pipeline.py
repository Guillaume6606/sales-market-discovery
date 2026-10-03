from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker

from ingestion import ingestion, listing_vision
from ingestion.valuation import evaluate_valuation
from libs.common.models import ListingDetail, ListingDetailORM, ListingObservation, ProductTemplate
from libs.common.settings import settings
from libs.common.vision_schema import VisionExtraction, VisionResult


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_succeeds", [False, True])
async def test_unchanged_summary_refreshes_expired_detail_before_vision(
    integration_db, seed_product, monkeypatch, refresh_succeeds
):
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_shadow_mode", False)
    monkeypatch.setattr(settings, "detail_fetch_enabled", True)
    factory = sessionmaker(bind=integration_db.bind)
    monkeypatch.setattr(ingestion, "SessionLocal", factory)
    monkeypatch.setattr(listing_vision, "SessionLocal", factory)
    now = datetime.now(UTC)
    obs = ListingObservation(
        product_id=seed_product,
        source="ebay",
        listing_id="unchanged-summary",
        title="iPhone 14 Pro 128GB",
        price=Decimal("100"),
        currency="EUR",
        condition="good",
        shipping_cost=Decimal("0"),
        is_sold=False,
        is_stale=False,
        last_seen_at=now,
        updated_at=now - timedelta(days=2),
        url="https://www.ebay.fr/itm/123",
    )
    integration_db.add(obs)
    integration_db.flush()
    detail = ListingDetailORM(
        obs_id=obs.obs_id,
        description="Working phone",
        photo_urls=[],
        fetched_at=now - timedelta(minutes=settings.alert_freshness_minutes + 1),
    )
    integration_db.add(detail)
    product = integration_db.get(ProductTemplate, seed_product)
    extraction = VisionExtraction(
        item_class="exact_device",
        model="iPhone 14 Pro 128GB",
        variant=None,
        included_accessories=[],
        seller_reported_faults=[],
        visible_damage=[],
        text_photo_conflict=False,
    )
    obs.vision_result = {
        **VisionResult(status="completed", extraction=extraction).model_dump(mode="json"),
        "pipeline": "vision",
        "checked_at": now.isoformat(),
        "input_fingerprint": listing_vision.input_fingerprint(obs, detail, product),
    }
    integration_db.commit()
    old_fetched_at = detail.fetched_at
    fetch = AsyncMock(
        return_value=ListingDetail(obs_id=obs.obs_id, description="Screen broken", photo_urls=[]),
        side_effect=None if refresh_succeeds else RuntimeError("detail unavailable"),
    )
    monkeypatch.setattr("ingestion.connectors.ebay.fetch_detail", fetch)
    from ingestion.detail_fetch import RATE_LIMITS

    monkeypatch.setitem(RATE_LIMITS, "ebay", 0)
    monkeypatch.setattr(
        "ingestion.computation.compute_pmn_for_product",
        lambda *args: {"status": "insufficient_data"},
    )
    monkeypatch.setattr(
        "ingestion.computation.compute_liquidity_score", lambda *args: {"error": "unmeasured"}
    )
    monkeypatch.setattr(
        "ingestion.composite_scoring.run_scoring_batch",
        AsyncMock(return_value={"status": "success"}),
    )
    monkeypatch.setattr("ingestion.alert_engine.trigger_alerts", AsyncMock(return_value=[]))
    extraction.seller_reported_faults = ["Screen broken"]
    extract = AsyncMock(return_value=VisionResult(status="completed", extraction=extraction))
    monkeypatch.setattr("libs.common.vision_service.extract_listing", extract)
    result = await ingestion.finish_product_pipeline(seed_product, ["ebay"])
    fetch.assert_awaited_once_with(obs.listing_id, obs_id=obs.obs_id)
    integration_db.refresh(obs)
    integration_db.refresh(detail)
    if refresh_succeeds:
        assert result["details"] == 1
        assert detail.description == "Screen broken"
        assert detail.fetched_at > old_fetched_at
        assert "Screen broken" in extract.await_args.args[1]
        assert listing_vision.vision_review_reasons(obs, detail, product) == [
            "vision_seller_faults_review_required"
        ]
    else:
        assert result["details"] == 0
        assert detail.fetched_at == old_fetched_at
        extract.assert_not_awaited()
        assert obs.vision_result["status"] == "pending"
        assert listing_vision.vision_review_reasons(obs, detail, product) == ["vision_stale"]


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [True, False])
async def test_pipeline_persists_v3_and_preserves_shadow_baseline(
    integration_db, seed_product, monkeypatch, shadow
):
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_shadow_mode", shadow)
    monkeypatch.setattr(listing_vision, "SessionLocal", sessionmaker(bind=integration_db.bind))
    obs = ListingObservation(
        product_id=seed_product,
        source="ebay",
        listing_id="vision-evidence",
        title="iPhone 14 Pro 128GB",
        price=Decimal("100"),
        currency="EUR",
        condition="good",
        shipping_cost=Decimal("0"),
        is_sold=False,
        is_stale=False,
        last_seen_at=datetime.now(UTC),
        url="https://www.ebay.fr/itm/123",
        llm_validated=True,
        llm_validation_result={"baseline": "preserve"},
    )
    integration_db.add(obs)
    integration_db.flush()
    detail = ListingDetailORM(
        obs_id=obs.obs_id,
        description="Boite vide",
        photo_urls=["https://i.ebayimg.com/a.jpg"],
        fetched_at=datetime.now(UTC),
    )
    integration_db.add(detail)
    integration_db.commit()
    extraction = VisionExtraction(
        item_class="accessory",
        model=None,
        variant=None,
        included_accessories=[],
        seller_reported_faults=[],
        visible_damage=[],
        text_photo_conflict=False,
    )
    call = AsyncMock(
        return_value=VisionResult(status="completed", extraction=extraction, model="test-model")
    )
    monkeypatch.setattr("libs.common.vision_service.extract_listing", call)
    summary = await listing_vision.run_listing_vision_batch(seed_product)
    assert summary["analysed"] == 1
    integration_db.refresh(obs)
    assert obs.vision_result["extraction"]["item_class"] == "accessory"
    assert obs.llm_validation_result == {"baseline": "preserve"}
    product = integration_db.get(ProductTemplate, seed_product)
    reasons = listing_vision.vision_review_reasons(obs, detail, product)
    assert ("vision_accessory" in reasons) is (not shadow)
    valuation = evaluate_valuation(integration_db, obs)
    assert not valuation["eligible"]
    assert "france_delivery_unconfirmed" in valuation["reasons"]
    await listing_vision.run_listing_vision_batch(seed_product)
    assert call.await_count == 1
    detail.description = "Description changed"
    integration_db.commit()
    await listing_vision.run_listing_vision_batch(seed_product)
    assert call.await_count == 2


@pytest.mark.asyncio
async def test_historical_jsonb_result_is_stale(integration_db, seed_product, monkeypatch):
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_shadow_mode", False)
    product = integration_db.get(ProductTemplate, seed_product)
    obs = ListingObservation(
        product_id=seed_product,
        source="ebay",
        listing_id="historical-vision",
        title="iPhone 14 Pro 128GB",
        vision_result={
            "pipeline": "vision",
            "schema_version": "listing-vision-v2",
            "status": "completed",
            "checked_at": datetime.now(UTC).isoformat(),
            "extraction": {"model": "iPhone 14 Pro", "evidence": [], "unknown_fields": []},
        },
    )
    obs.vision_result["input_fingerprint"] = listing_vision.input_fingerprint(obs, None, product)
    integration_db.add(obs)
    integration_db.commit()
    integration_db.refresh(obs)
    assert obs.vision_result["extraction"]["evidence"] == []
    assert listing_vision.vision_review_reasons(obs, None, product) == ["vision_stale"]

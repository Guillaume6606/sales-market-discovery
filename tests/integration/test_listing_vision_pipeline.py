from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker

from ingestion import listing_vision
from ingestion.valuation import evaluate_valuation
from libs.common.models import ListingDetailORM, ListingObservation, ProductTemplate
from libs.common.settings import settings
from libs.common.vision_schema import VisionExtraction, VisionResult


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [True, False])
async def test_pipeline_persists_evidence_and_preserves_shadow_baseline(
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
        seller_condition_claims=[],
        visible_defects=[],
        contradictions=[],
        unknown_fields=["model"],
        evidence=[{"field": "item_class", "text_quote": "Boite vide"}],
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

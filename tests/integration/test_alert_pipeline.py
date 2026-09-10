"""Real PostgreSQL alert outbox, verified valuation and retry integration."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from ingestion.alert_engine import deliver_pending_alerts, trigger_alerts
from libs.common.models import AlertEvent, AlertRule, ListingObservation, ProductTemplate
from libs.common.valuation_models import VerifiedValuationReference


@pytest.fixture()
def opportunity(integration_db, seed_product, monkeypatch):
    monkeypatch.setattr("ingestion.alert_engine.settings.working_capital_eur", Decimal("2000"))
    now = datetime.now(UTC)
    listing = ListingObservation(
        product_id=seed_product,
        source="ebay",
        listing_id="bargain-001",
        title="iPhone 14 Pro 128GB",
        price=600,
        currency="EUR",
        condition="Used",
        is_sold=False,
        is_stale=False,
        shipping_cost=0,
        observed_at=now,
        last_seen_at=now,
        url="https://www.ebay.fr/itm/123",
        evidence_type="asking",
    )
    reference = VerifiedValuationReference(
        product_id=seed_product,
        purchase_source="ebay",
        destination_marketplace="leboncoin",
        required_tokens=["iphone", "14", "pro", "128gb"],
        excluded_tokens=["max", "parts"],
        condition="good",
        reviewed_price_eur=800,
        currency="EUR",
        evidence_url="https://example.com/verified-sale",
        comparable_sold_at=now - timedelta(days=3),
        reviewed_at=now - timedelta(days=1),
        reviewed_by="test operator",
        expires_at=now + timedelta(days=7),
        limitations="Test evidence",
        purchase_fee_rate=0,
        purchase_fee_fixed_eur=0,
        sell_fee_rate=Decimal(".05"),
        sell_fee_fixed_eur=0,
        social_charge_rate=Decimal(".10"),
        outbound_logistics_eur=10,
        risk_allowance_eur=10,
        minimum_contribution_eur=30,
        is_active=True,
    )
    rule = AlertRule(name="Test Bargain", threshold_pct=-10, min_margin_abs=30, is_active=True)
    integration_db.add_all([listing, reference, rule])
    integration_db.commit()
    return {
        "listing": listing,
        "product_template": integration_db.get(ProductTemplate, seed_product),
        "metrics": None,
        "reference": reference,
    }


@pytest.mark.asyncio
async def test_verified_alert_is_persisted_and_not_sent_twice(
    integration_db, opportunity, monkeypatch
):
    sender = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr("ingestion.alert_engine.send_opportunity_alert", sender)
    events = await trigger_alerts([opportunity], db=integration_db)
    assert len(events) == 1
    assert events[0].delivery_status == "sent"
    assert events[0].delivery_attempts == 1
    assert events[0].sent_at is not None
    assert Decimal(events[0].delivery["valuation"]["contribution_eur"]) == Decimal("60")
    assert await trigger_alerts([opportunity], db=integration_db) == []
    assert integration_db.query(AlertEvent).count() == 1
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_failure_retries_the_same_persisted_event(
    integration_db, opportunity, monkeypatch
):
    sender = AsyncMock(side_effect=[{"status": "error"}, {"status": "success"}])
    monkeypatch.setattr("ingestion.alert_engine.send_opportunity_alert", sender)
    events = await trigger_alerts([opportunity], db=integration_db)
    event = events[0]
    assert event.delivery_status == "failed"
    assert event.sent_at is None
    assert await trigger_alerts([opportunity], db=integration_db) == []
    assert sender.await_count == 1
    event.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    integration_db.commit()
    counts = await deliver_pending_alerts(db=integration_db)
    assert counts["sent"] == 1
    assert event.delivery_attempts == 2
    assert event.delivery_status == "sent"
    assert integration_db.query(AlertEvent).count() == 1


@pytest.mark.asyncio
async def test_expired_evidence_suppresses_retry(integration_db, opportunity, monkeypatch):
    sender = AsyncMock(return_value={"status": "error"})
    monkeypatch.setattr("ingestion.alert_engine.send_opportunity_alert", sender)
    event = (await trigger_alerts([opportunity], db=integration_db))[0]
    event.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    opportunity["reference"].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    integration_db.commit()
    counts = await deliver_pending_alerts(db=integration_db)
    assert counts["suppressed"] == 1
    assert event.delivery_status == "suppressed"
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_reference_never_sends(integration_db, opportunity, monkeypatch):
    opportunity["reference"].is_active = False
    integration_db.commit()
    sender = AsyncMock()
    monkeypatch.setattr("ingestion.alert_engine.send_opportunity_alert", sender)
    assert await trigger_alerts([opportunity], db=integration_db) == []
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_detail_scoring_and_alerts_run_in_one_product_pipeline(
    integration_db, opportunity, monkeypatch
):
    from sqlalchemy.orm import sessionmaker

    from ingestion.ingestion import finish_product_pipeline
    from libs.common.models import ListingDetail, ListingDetailORM, ListingScore

    factory = sessionmaker(bind=integration_db.get_bind())
    monkeypatch.setattr("libs.common.db.SessionLocal", factory)
    monkeypatch.setattr("ingestion.ingestion.SessionLocal", factory)
    monkeypatch.setitem(
        __import__("ingestion.detail_fetch", fromlist=["RATE_LIMITS"]).RATE_LIMITS, "ebay", 0
    )
    detail_fetch = AsyncMock(
        return_value=ListingDetail(
            obs_id=opportunity["listing"].obs_id,
            description="Working iPhone 14 Pro 128GB",
            photo_urls=["https://example.com/photo.jpg"],
        )
    )
    monkeypatch.setattr("ingestion.connectors.ebay.fetch_detail", detail_fetch)
    sender = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr("ingestion.alert_engine.send_opportunity_alert", sender)
    result = await finish_product_pipeline(
        str(opportunity["product_template"].product_id), ["ebay"]
    )
    assert result["details"] == 1
    assert result["scoring"]["scored"] == 1
    assert result["alerts"] == 1
    integration_db.expire_all()
    assert integration_db.query(ListingDetailORM).count() == 1
    score = integration_db.query(ListingScore).one()
    assert score.arbitrage_spread_eur == Decimal("60")
    detail_fetch.assert_awaited_once()
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_inventory_cost_blocks_purchase_above_remaining_capital(
    integration_db, opportunity, monkeypatch
):
    from datetime import date

    from libs.common.trade_models import Trade

    monkeypatch.setattr("ingestion.alert_engine.settings.working_capital_eur", Decimal("1000"))
    integration_db.add(
        Trade(
            title="Existing purchase",
            buy_platform="ebay",
            exit_platform="leboncoin",
            acquired_on=date.today(),
            acquisition_price_eur=450,
            status="purchased",
        )
    )
    integration_db.commit()
    sender = AsyncMock()
    monkeypatch.setattr("ingestion.alert_engine.send_opportunity_alert", sender)
    assert await trigger_alerts([opportunity], db=integration_db) == []
    sender.assert_not_awaited()


def test_detail_api_revalidates_cached_profit_after_reference_expires(integration_db, opportunity):
    from backend.routers.listing_detail import get_listing_detail
    from libs.common.models import ListingScore

    obs_id = opportunity["listing"].obs_id
    integration_db.add(
        ListingScore(
            obs_id=obs_id,
            product_id=opportunity["listing"].product_id,
            arbitrage_spread_eur=999,
            risk_adjusted_confidence=99,
        )
    )
    integration_db.commit()
    assert get_listing_detail(obs_id, integration_db)["score"]["arbitrage_spread_eur"] == 60
    opportunity["reference"].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    integration_db.commit()
    result = get_listing_detail(obs_id, integration_db)["score"]
    assert result["arbitrage_spread_eur"] is None
    assert "reference_expired" in result["score_breakdown"]["verified_valuation"]["reasons"]


@pytest.mark.asyncio
async def test_rule_preview_works_without_pmn(integration_db, opportunity):
    from backend.main import test_alert_rule

    rule = integration_db.query(AlertRule).one()
    result = await test_alert_rule(str(rule.rule_id), integration_db)
    assert result["match_count"] == 1


@pytest.mark.asyncio
async def test_archiving_rule_preserves_event_and_stops_retries(
    integration_db, opportunity, monkeypatch
):
    from backend.main import delete_alert_rule

    monkeypatch.setattr(
        "ingestion.alert_engine.send_opportunity_alert", AsyncMock(return_value={"status": "error"})
    )
    event = (await trigger_alerts([opportunity], db=integration_db))[0]
    delete_alert_rule(str(event.rule_id), integration_db)
    event.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    integration_db.commit()
    assert (await deliver_pending_alerts(db=integration_db))["suppressed"] == 1
    assert integration_db.query(AlertEvent).count() == 1


@pytest.mark.asyncio
async def test_delivered_alert_feedback_records_a_loss(integration_db, opportunity, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.routers.feedback import router
    from libs.common.db import get_db

    monkeypatch.setattr(
        "ingestion.alert_engine.send_opportunity_alert",
        AsyncMock(return_value={"status": "success"}),
    )
    event = (await trigger_alerts([opportunity], db=integration_db))[0]
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: integration_db
    client = TestClient(app)
    path = f"/alerts/events/{event.alert_id}/feedback"
    assert client.post(path, json={"feedback": "purchased"}).status_code == 201
    feedback = client.get(path).json()["feedback"]
    response = client.patch(f"/alerts/feedback/{feedback['feedback_id']}", json={"profit": -25})
    assert response.status_code == 200
    assert client.get(path).json()["feedback"]["profit"] == -25


@pytest.mark.asyncio
async def test_exhausted_delivery_does_not_advertise_another_retry(
    integration_db, opportunity, monkeypatch
):
    monkeypatch.setattr("ingestion.alert_engine.settings.alert_max_attempts", 1)
    sender = AsyncMock(return_value={"status": "error"})
    monkeypatch.setattr("ingestion.alert_engine.send_opportunity_alert", sender)
    event = (await trigger_alerts([opportunity], db=integration_db))[0]
    assert event.delivery_status == "failed"
    assert event.next_attempt_at is None
    await deliver_pending_alerts(db=integration_db)
    sender.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_status", ["failed", "suppressed"])
async def test_operator_can_retry_a_recovered_alert(
    integration_db, opportunity, monkeypatch, previous_status
):
    from backend.main import retry_alert_event

    monkeypatch.setattr("ingestion.alert_engine.settings.alert_max_attempts", 1)
    sender = AsyncMock(side_effect=[{"status": "error"}, {"status": "success"}])
    monkeypatch.setattr("ingestion.alert_engine.send_opportunity_alert", sender)
    event = (await trigger_alerts([opportunity], db=integration_db))[0]
    event.delivery_status = previous_status
    event.suppressed = previous_status == "suppressed"
    integration_db.commit()
    result = retry_alert_event(event.alert_id, integration_db)
    assert result["delivery_status"] == "pending"
    assert event.delivery_attempts == 0
    sender.assert_awaited_once()
    assert (await deliver_pending_alerts(db=integration_db))["sent"] == 1
    assert integration_db.query(AlertEvent).count() == 1
    assert len(event.delivery["manual_retries"]) == 1
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        retry_alert_event(event.alert_id, integration_db)
    assert error.value.status_code == 409


def test_current_best_listing_is_not_hidden_by_cached_top_500(integration_db, opportunity):
    from backend.routers.scored_listings import scored_listings
    from libs.common.models import ListingScore

    now = datetime.now(UTC)
    product_id = opportunity["listing"].product_id
    distractors = [
        ListingObservation(
            product_id=product_id,
            source="ebay",
            listing_id=f"wrong-variant-{i}",
            title="iPhone 14 Pro Max 128GB",
            price=600,
            currency="EUR",
            condition="Used",
            shipping_cost=0,
            is_sold=False,
            is_stale=False,
            observed_at=now,
            last_seen_at=now,
            url="https://www.ebay.fr/itm/1",
        )
        for i in range(500)
    ]
    integration_db.add_all(distractors)
    integration_db.flush()
    integration_db.add_all(
        [
            ListingScore(
                obs_id=row.obs_id,
                product_id=product_id,
                arbitrage_spread_eur=999,
                risk_adjusted_confidence=99,
            )
            for row in distractors
        ]
    )
    integration_db.add(
        ListingScore(
            obs_id=opportunity["listing"].obs_id,
            product_id=product_id,
            arbitrage_spread_eur=0,
            risk_adjusted_confidence=0,
        )
    )
    integration_db.commit()
    rows = scored_listings(str(product_id), min_confidence=0, limit=1, db=integration_db)
    assert [row["obs_id"] for row in rows] == [opportunity["listing"].obs_id]

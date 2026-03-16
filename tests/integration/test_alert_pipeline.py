"""Integration test: Alert pipeline with real SQLite database."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from libs.common.models import (
    AlertEvent,
    AlertRule,
    ListingObservation,
    MarketPriceNormal,
)


@pytest.fixture()
def seed_pmn(integration_db, seed_product):
    """Seed a PMN record with high confidence."""
    pmn = MarketPriceNormal(
        product_id=seed_product,
        pmn=750.0,
        pmn_low=700.0,
        pmn_high=800.0,
        last_computed_at=datetime.now(UTC),
        confidence=0.8,
        methodology={"method": "median_std"},
    )
    integration_db.add(pmn)
    integration_db.commit()
    return pmn


@pytest.fixture()
def seed_alert_rule(integration_db):
    """Seed an active alert rule."""
    rule_id = str(uuid.uuid4())
    rule = AlertRule(
        rule_id=rule_id,
        name="Test Bargain Rule",
        threshold_pct=-10.0,
        min_margin_abs=20.0,
        is_active=True,
        channels=None,  # ARRAY not supported in SQLite
    )
    integration_db.add(rule)
    integration_db.commit()
    return rule


@pytest.fixture()
def seed_cheap_listing(integration_db, seed_product):
    """Seed a listing below PMN."""
    obs = ListingObservation(
        product_id=seed_product,
        source="ebay",
        listing_id="bargain-001",
        title="iPhone 14 Pro Cheap",
        price=600.0,
        currency="EUR",
        condition="Used",
        is_sold=False,
        seller_rating=4.5,
        observed_at=datetime.now(UTC),
    )
    integration_db.add(obs)
    integration_db.commit()
    return obs


class TestAlertPipeline:
    @pytest.mark.asyncio
    async def test_trigger_alerts_creates_event(
        self,
        integration_db,
        seed_product,
        seed_pmn,
        seed_alert_rule,
        seed_cheap_listing,
    ):
        """Alert pipeline creates AlertEvent for listing below PMN."""
        from libs.common.models import ProductTemplate

        product = (
            integration_db.query(ProductTemplate)
            .filter(ProductTemplate.product_id == seed_product)
            .first()
        )

        opportunities = [
            {
                "listing": seed_cheap_listing,
                "product_template": product,
                "pmn_data": seed_pmn,
                "metrics": None,
            }
        ]

        with patch(
            "ingestion.alert_engine.send_opportunity_alert",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        ):
            from ingestion.alert_engine import trigger_alerts

            events = await trigger_alerts(opportunities, db=integration_db)

        assert len(events) >= 1
        # At least one non-suppressed event
        non_suppressed = [e for e in events if not e.suppressed]
        assert len(non_suppressed) >= 1

        # Verify persisted in DB
        db_events = integration_db.query(AlertEvent).all()
        assert len(db_events) >= 1

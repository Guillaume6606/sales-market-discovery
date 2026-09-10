from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.routers.feedback import FeedbackUpdate
from ingestion.alert_engine import _rule_matches
from ingestion.connectors import leboncoin_api
from libs.common.models import AlertRule, ListingObservation, ProductTemplate


def test_feedback_can_record_a_loss():
    assert FeedbackUpdate(profit=-25).profit == -25


@pytest.mark.asyncio
async def test_unsupported_sold_feed_never_fetches_asking_prices(monkeypatch):
    fetch = AsyncMock(return_value=[object()])
    monkeypatch.setattr(leboncoin_api, "fetch_leboncoin_api_listings", fetch)
    assert await leboncoin_api.fetch_leboncoin_api_sold("camera") == []
    fetch.assert_not_awaited()


def objects(price=99, stale=False):
    rule = AlertRule(threshold_pct=10, min_margin_abs=None)
    listing = ListingObservation(
        price=Decimal(price),
        is_sold=False,
        is_stale=stale,
        last_seen_at=datetime.now(UTC),
        currency="EUR",
    )
    product = ProductTemplate(is_active=True)
    pmn = SimpleNamespace(pmn=100, confidence=1, methodology={"data_source": "sold_0_active_30"})
    return rule, listing, product, pmn


def test_asking_pmn_cannot_authorize_an_alert():
    assert not _rule_matches(*objects(), None)


def test_stale_listing_is_rejected_even_with_a_discount():
    assert not _rule_matches(*objects(price=50, stale=True), None)


def test_missing_pmn_cannot_bypass_margin_requirement():
    rule, listing, product, _ = objects()
    assert not _rule_matches(rule, listing, product, None, None)


def valuation():
    return {
        "eligible": True,
        "reference_id": 1,
        "estimated_sale_price_eur": Decimal("100"),
        "contribution_eur": Decimal("25"),
        "acquisition_cost_eur": Decimal("50"),
        "max_buy_price_eur": Decimal("75"),
        "reasons": [],
    }


def test_positive_legacy_discount_is_normalized():
    rule, listing, product, pmn = objects()
    assert not _rule_matches(rule, listing, product, pmn, None, valuation=valuation())


def test_verified_contribution_meets_rule():
    rule, listing, product, _ = objects(price=50)
    rule.min_margin_abs = 20
    assert _rule_matches(rule, listing, product, None, None, valuation=valuation())


def test_minimum_margin_uses_net_contribution():
    rule, listing, product, _ = objects(price=50)
    rule.min_margin_abs = 30
    assert not _rule_matches(rule, listing, product, None, None, valuation=valuation())


def test_old_listing_is_rejected():
    rule, listing, product, _ = objects(price=50)
    listing.last_seen_at = datetime.now(UTC) - timedelta(days=2)
    assert not _rule_matches(rule, listing, product, None, None, valuation=valuation())


def test_required_liquidity_missing_fails_closed():
    rule, listing, product, _ = objects(price=50)
    rule.min_liquidity_score = 50
    assert not _rule_matches(rule, listing, product, None, None, valuation=valuation())


def test_nonfinite_prices_are_rejected(listing_factory):
    from ingestion.validation import validate_listing

    assert validate_listing(listing_factory(price=float("nan"))) is not None
    assert validate_listing(listing_factory(price=float("inf"))) is not None


def test_capital_gate_requires_explicit_budget(monkeypatch):
    from ingestion.alert_engine import apply_capital_gate

    monkeypatch.setattr("ingestion.alert_engine.settings.working_capital_eur", None)
    result = apply_capital_gate(valuation(), Decimal("0"))
    assert not result["eligible"]
    assert "working_capital_unconfigured" in result["reasons"]


def test_capital_gate_accounts_for_inventory(monkeypatch):
    from ingestion.alert_engine import apply_capital_gate

    monkeypatch.setattr("ingestion.alert_engine.settings.working_capital_eur", Decimal("100"))
    assert apply_capital_gate(valuation(), Decimal("50"))["eligible"]
    result = apply_capital_gate(valuation(), Decimal("51"))
    assert not result["eligible"]
    assert "insufficient_available_capital" in result["reasons"]


@pytest.mark.asyncio
async def test_vinted_failure_is_not_reported_as_empty_market(monkeypatch):
    from ingestion.connectors.vinted_api import VintedAPIConnector

    monkeypatch.setattr(
        "vinted_scraper.AsyncVintedScraper.create", AsyncMock(side_effect=RuntimeError("offline"))
    )
    with pytest.raises(RuntimeError):
        await VintedAPIConnector().search_items("test product")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_shipping_is_rejected(listing_factory, value):
    from ingestion.validation import validate_listing

    assert validate_listing(listing_factory(shipping_cost=value)) is not None


def test_rule_input_rejects_nonfinite_and_invalid_scales():
    from pydantic import ValidationError

    from backend.main import AlertRuleCreate

    with pytest.raises(ValidationError):
        AlertRuleCreate(name="invalid", threshold_pct=float("nan"))
    with pytest.raises(ValidationError):
        AlertRuleCreate(name="invalid", min_liquidity_score=101)


def test_rule_without_telegram_channel_cannot_send_telegram():
    rule, listing, product, _ = objects(price=50)
    rule.channels = []
    assert not _rule_matches(rule, listing, product, None, None, valuation=valuation())

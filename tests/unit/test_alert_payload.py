from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from ingestion.alert_engine import _alert_payload, _contribution_at
from ingestion.valuation import _evaluate_reference

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _reference(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "reference_id": "ref-1",
        "purchase_source": "leboncoin",
        "destination_marketplace": "ebay",
        "required_tokens": ["sony", "24", "gm", "ii"],
        "excluded_tokens": [],
        "condition": "good",
        "reviewed_price_eur": Decimal("1150"),
        "currency": "EUR",
        "evidence_url": "https://example.com/sold",
        "comparable_sold_at": NOW - timedelta(days=3),
        "reviewed_at": NOW - timedelta(days=1),
        "reviewed_by": "operator",
        "expires_at": NOW + timedelta(days=7),
        "limitations": "test",
        "purchase_fee_rate": Decimal("0.04"),
        "purchase_fee_fixed_eur": Decimal("0.79"),
        "sell_fee_rate": Decimal("0.13"),
        "sell_fee_fixed_eur": Decimal("0.35"),
        "social_charge_rate": Decimal("0.123"),
        "outbound_logistics_eur": Decimal("15"),
        "risk_allowance_eur": Decimal("40"),
        "minimum_contribution_eur": Decimal("60"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _listing(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "obs_id": 1,
        "product_id": "p1",
        "listing_id": "lbc-1",
        "source": "leboncoin",
        "title": "Sony FE 24-70mm f/2.8 GM II",
        "url": "https://www.leboncoin.fr/ad/1",
        "price": Decimal("700"),
        "shipping_cost": Decimal("12"),
        "currency": "EUR",
        "condition": "Très bon état",
        "seller_rating": None,
        "first_seen_at": NOW - timedelta(hours=2),
        "screenshot_path": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


PRODUCT = SimpleNamespace(
    product_id="p1", name="Sony FE 24-70mm f/2.8 GM II", brand="Sony", description=None
)


def _valuation(reference: SimpleNamespace, listing: SimpleNamespace) -> dict[str, Any]:
    return _evaluate_reference(reference, listing, NOW, [])


def test_contribution_at_ask_matches_the_valuation() -> None:
    reference, listing = _reference(), _listing()
    valuation = _valuation(reference, listing)
    assert _contribution_at(valuation, reference, listing.price) == valuation["contribution_eur"]


def test_payload_figures_are_consistent_with_max_buy_and_offer() -> None:
    reference, listing = _reference(), _listing()
    valuation = _valuation(reference, listing)
    opportunity, listing_data, product_data = _alert_payload(valuation, listing, PRODUCT, reference)

    max_buy = valuation["max_buy_price_eur"]
    minimum = reference.minimum_contribution_eur
    assert opportunity["max_buy_price_eur"] == max_buy
    assert minimum <= opportunity["contribution_at_max_buy_eur"] < minimum + Decimal("0.05")
    assert 0 < opportunity["offer_price_eur"] <= max_buy
    assert opportunity["offer_price_eur"] < listing.price
    assert opportunity["contribution_at_offer_eur"] >= opportunity["contribution_at_max_buy_eur"]
    assert opportunity["contribution_eur"] == valuation["contribution_eur"]
    assert opportunity["estimated_sale_price_eur"] == valuation["estimated_sale_price_eur"]
    assert opportunity["minimum_contribution_eur"] == minimum
    assert opportunity["reference_condition"] == "good"
    assert opportunity["destination_marketplace"] == "ebay"

    assert listing_data["shipping_cost"] == listing.shipping_cost
    assert listing_data["first_seen_at"] == listing.first_seen_at
    assert listing_data["source"] == "leboncoin"
    assert listing_data["price"] == listing.price
    assert product_data["name"] == PRODUCT.name


def test_missing_reference_keeps_offer_but_omits_derived_contributions() -> None:
    reference, listing = _reference(), _listing()
    valuation = _valuation(reference, listing)
    opportunity, _, _ = _alert_payload(valuation, listing, PRODUCT, None)
    assert opportunity["offer_price_eur"] is not None
    assert opportunity["contribution_at_max_buy_eur"] is None
    assert opportunity["contribution_at_offer_eur"] is None


@pytest.mark.parametrize("shipping", [None, Decimal("0")])
def test_payload_handles_unknown_or_free_shipping(shipping: Decimal | None) -> None:
    reference = _reference()
    listing = _listing(shipping_cost=shipping)
    valuation = _valuation(reference, listing)
    opportunity, listing_data, _ = _alert_payload(valuation, listing, PRODUCT, reference)
    assert listing_data["shipping_cost"] == shipping
    if shipping is None:
        assert opportunity["contribution_at_offer_eur"] is None

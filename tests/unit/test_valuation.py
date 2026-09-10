from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import ANY
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.valuation import router
from ingestion.valuation import evaluate_valuation

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
PRODUCT_ID = UUID("00000000-0000-0000-0000-000000000001")


class _ReferenceQuery:
    def __init__(self, references: list[SimpleNamespace]) -> None:
        self.references = references

    def filter(self, *_conditions: object) -> "_ReferenceQuery":
        return self

    def order_by(self, *_columns: object) -> "_ReferenceQuery":
        return self

    def all(self) -> list[SimpleNamespace]:
        return self.references


class _Session:
    def __init__(
        self,
        references: list[SimpleNamespace],
        reviews: list[SimpleNamespace] | None = None,
    ) -> None:
        self.references = references
        self.reviews = reviews or []

    def query(self, model: object) -> _ReferenceQuery:
        values = (
            self.reviews
            if getattr(model, "__name__", "") == "ValuationListingReview"
            else self.references
        )
        return _ReferenceQuery(values)


def _observation(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "obs_id": 41,
        "product_id": PRODUCT_ID,
        "source": "leboncoin",
        "title": "Sony WH-1000XM5 casque noir",
        "url": "https://example.com/listing/41",
        "price": Decimal("300.00"),
        "shipping_cost": Decimal("10.00"),
        "currency": "EUR",
        "condition": "Très bon état",
        "is_sold": False,
        "is_stale": False,
        "last_seen_at": NOW - timedelta(minutes=5),
        "updated_at": NOW - timedelta(minutes=5),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _reference(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "reference_id": uuid4(),
        "product_id": PRODUCT_ID,
        "purchase_source": "leboncoin",
        "destination_marketplace": "ebay",
        "required_tokens": ["sony", "wh", "1000xm5"],
        "excluded_tokens": ["pieces", "etui"],
        "condition": "like_new",
        "reviewed_price_eur": Decimal("500.00"),
        "currency": "EUR",
        "evidence_url": "https://example.com/sold/1",
        "comparable_sold_at": NOW - timedelta(days=4),
        "reviewed_at": NOW - timedelta(days=2),
        "reviewed_by": "operator@example.com",
        "expires_at": NOW + timedelta(days=28),
        "limitations": "Exact model and condition; colour may differ.",
        "purchase_fee_rate": Decimal("0.02"),
        "purchase_fee_fixed_eur": Decimal("5.00"),
        "sell_fee_rate": Decimal("0.10"),
        "sell_fee_fixed_eur": Decimal("3.00"),
        "social_charge_rate": Decimal("0.123"),
        "outbound_logistics_eur": Decimal("20.00"),
        "risk_allowance_eur": Decimal("15.00"),
        "minimum_contribution_eur": Decimal("20.00"),
        "is_active": True,
        "created_at": NOW - timedelta(days=2),
        "updated_at": NOW - timedelta(days=2),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _review(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "review_id": uuid4(),
        "obs_id": 41,
        "reviewed_title": "Sony WH-1000XM5 casque noir",
        "reviewed_condition": "like_new",
        "reviewed_shipping_cost_eur": Decimal("10.00"),
        "reviewed_by": "operator@example.com",
        "reviewed_at": NOW - timedelta(minutes=10),
        "expires_at": NOW + timedelta(hours=2),
        "notes": "Checked the live listing.",
        "raw_title": "Sony headset",
        "raw_price_eur": Decimal("300.00"),
        "raw_condition": None,
        "raw_shipping_cost_eur": None,
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_evaluate_valuation_uses_decimal_net_contribution_arithmetic() -> None:
    reference = _reference()

    result = evaluate_valuation(_Session([reference]), _observation(), now=NOW)

    assert result["eligible"] is True
    assert result["reasons"] == []
    assert result["reference_id"] == str(reference.reference_id)
    assert result["estimated_sale_price_eur"] == Decimal("500.00")
    assert result["acquisition_cost_eur"] == Decimal("321.00")
    assert result["contribution_eur"] == Decimal("29.50")
    assert result["max_buy_price_eur"] == Decimal("309.31")
    assert result["cost_breakdown"] == {
        "listing_price_eur": Decimal("300.00"),
        "inbound_shipping_eur": Decimal("10.00"),
        "purchase_fee_eur": Decimal("11.00"),
        "sell_fee_eur": Decimal("53.00"),
        "social_charge_eur": Decimal("61.50"),
        "outbound_logistics_eur": Decimal("20.00"),
        "risk_allowance_eur": Decimal("15.00"),
        "minimum_contribution_eur": Decimal("20.00"),
    }


def test_evaluate_valuation_matches_whole_normalized_tokens() -> None:
    reference = _reference(required_tokens=["pro"], excluded_tokens=["case"])
    observation = _observation(title="Sony product showcase")

    result = evaluate_valuation(_Session([reference]), observation, now=NOW)

    assert result["eligible"] is False
    assert "required_tokens_missing" in result["reasons"]
    assert "excluded_tokens_present" not in result["reasons"]


@pytest.mark.parametrize(
    ("reference_overrides", "expected_reason"),
    [
        ({"expires_at": NOW}, "reference_expired"),
        ({"currency": "USD"}, "reference_currency_not_eur"),
        ({"condition": "good"}, "condition_mismatch"),
        ({"reviewed_by": ""}, "reference_evidence_incomplete"),
        ({"evidence_url": "not-a-url"}, "reference_evidence_invalid"),
        (
            {
                "comparable_sold_at": NOW + timedelta(hours=1),
                "reviewed_at": NOW + timedelta(hours=2),
                "expires_at": NOW + timedelta(days=2),
            },
            "reference_evidence_in_future",
        ),
        ({"sell_fee_rate": Decimal("Infinity")}, "reference_costs_invalid"),
    ],
)
def test_evaluate_valuation_rejects_unusable_reference(
    reference_overrides: dict[str, object], expected_reason: str
) -> None:
    result = evaluate_valuation(
        _Session([_reference(**reference_overrides)]), _observation(), now=NOW
    )

    assert result["eligible"] is False
    assert expected_reason in result["reasons"]


@pytest.mark.parametrize(
    ("observation_overrides", "expected_reason"),
    [
        ({"shipping_cost": None}, "listing_missing_shipping_cost"),
        ({"condition": None}, "listing_condition_unknown"),
        ({"currency": "USD"}, "listing_currency_not_eur"),
        ({"is_stale": True}, "listing_stale"),
        ({"url": None}, "listing_url_missing"),
        ({"price": Decimal("NaN")}, "listing_price_invalid"),
        ({"last_seen_at": NOW - timedelta(days=2)}, "listing_last_seen_stale"),
    ],
)
def test_evaluate_valuation_rejects_incomplete_listing(
    observation_overrides: dict[str, object], expected_reason: str
) -> None:
    result = evaluate_valuation(
        _Session([_reference()]), _observation(**observation_overrides), now=NOW
    )

    assert result["eligible"] is False
    assert expected_reason in result["reasons"]


def test_evaluate_valuation_uses_reviewed_inputs_without_overwriting_raw_listing() -> None:
    observation = _observation(
        title="Sony headset",
        condition=None,
        shipping_cost=None,
    )

    result = evaluate_valuation(_Session([_reference()], reviews=[_review()]), observation, now=NOW)

    assert result["eligible"] is True
    assert result["listing_review_snapshot"] == {
        "review_id": ANY,
        "reviewed_title": "Sony WH-1000XM5 casque noir",
        "reviewed_condition": "like_new",
        "reviewed_shipping_cost_eur": Decimal("10.00"),
        "reviewed_by": "operator@example.com",
        "reviewed_at": NOW - timedelta(minutes=10),
        "expires_at": NOW + timedelta(hours=2),
        "notes": "Checked the live listing.",
        "raw_title": "Sony headset",
        "raw_price_eur": Decimal("300.00"),
        "raw_condition": None,
        "raw_shipping_cost_eur": None,
    }
    assert observation.title == "Sony headset"
    assert observation.condition is None
    assert observation.shipping_cost is None


def test_evaluate_valuation_ignores_expired_listing_review() -> None:
    observation = _observation(condition=None, shipping_cost=None)
    expired_review = _review(expires_at=NOW)

    result = evaluate_valuation(
        _Session([_reference()], reviews=[expired_review]), observation, now=NOW
    )

    assert result["eligible"] is False
    assert "listing_condition_unknown" in result["reasons"]
    assert "listing_missing_shipping_cost" in result["reasons"]
    assert result["listing_review_snapshot"] is None


def test_evaluate_valuation_ignores_review_after_listing_price_changes() -> None:
    observation = _observation(
        title="Sony headset",
        condition=None,
        shipping_cost=None,
        price=Decimal("290.00"),
    )

    result = evaluate_valuation(_Session([_reference()], reviews=[_review()]), observation, now=NOW)

    assert result["eligible"] is False
    assert result["listing_review_snapshot"] is None


def test_evaluate_valuation_filters_purchase_source_and_chooses_best_destination() -> None:
    wrong_source = _reference(
        purchase_source="vinted",
        reviewed_price_eur=Decimal("900.00"),
        reviewed_at=NOW - timedelta(hours=1),
    )
    ebay = _reference(destination_marketplace="ebay", reviewed_price_eur=Decimal("500.00"))
    vinted = _reference(
        destination_marketplace="vinted",
        reviewed_price_eur=Decimal("520.00"),
        sell_fee_rate=Decimal("0.08"),
        reviewed_at=NOW - timedelta(days=3),
    )

    result = evaluate_valuation(_Session([wrong_source, ebay, vinted]), _observation(), now=NOW)

    assert result["eligible"] is True
    assert result["reference_id"] == str(vinted.reference_id)
    assert result["destination_marketplace"] == "vinted"
    assert result["contribution_eur"] == Decimal("55.44")


def test_evaluate_valuation_uses_latest_reference_as_deterministic_tie_break() -> None:
    older = _reference(reviewed_at=NOW - timedelta(days=3))
    latest = _reference(reviewed_at=NOW - timedelta(days=1))

    result = evaluate_valuation(_Session([older, latest]), _observation(), now=NOW)

    assert result["reference_id"] == str(latest.reference_id)


def test_evaluate_valuation_returns_negative_contribution_but_blocks_recommendation() -> None:
    reference = _reference(reviewed_price_eur=Decimal("400.00"))

    result = evaluate_valuation(_Session([reference]), _observation(), now=NOW)

    assert result["eligible"] is False
    assert result["contribution_eur"] == Decimal("-48.20")
    assert result["reasons"] == ["below_minimum_contribution"]


def test_max_buy_price_rounds_down_to_preserve_minimum_contribution() -> None:
    reference = _reference(sell_fee_fixed_eur=Decimal("0.01"))
    result = evaluate_valuation(_Session([reference]), _observation(), now=NOW)

    assert result["max_buy_price_eur"] == Decimal("312.24")

    at_cap = evaluate_valuation(
        _Session([reference]),
        _observation(price=result["max_buy_price_eur"]),
        now=NOW,
    )
    assert at_cap["eligible"] is True
    assert at_cap["contribution_eur"] >= Decimal("20.00")


def test_reference_api_rejects_implicit_or_invalid_financial_inputs() -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    payload = {
        "product_id": str(PRODUCT_ID),
        "purchase_source": "leboncoin",
        "destination_marketplace": "ebay",
        "required_tokens": ["wh-1000xm5"],
        "excluded_tokens": [],
        "condition": "like_new",
        "reviewed_price_eur": "500.00",
        "currency": "EUR",
        "evidence_url": "https://example.com/sold/1",
        "comparable_sold_at": "2026-09-05T12:00:00Z",
        "reviewed_at": "2026-09-08T12:00:00Z",
        "reviewed_by": "operator@example.com",
        "expires_at": "2026-10-08T12:00:00Z",
        "purchase_fee_rate": "1.01",
        "purchase_fee_fixed_eur": "0.00",
        "sell_fee_rate": "0.10",
        "sell_fee_fixed_eur": "0.00",
        "social_charge_rate": "0.123",
        "outbound_logistics_eur": "20.00",
        "risk_allowance_eur": "15.00",
        "minimum_contribution_eur": "20.00",
        "limitations": "Exact model and condition.",
    }

    response = client.post("/valuation/references", json=payload)

    assert response.status_code == 422


def test_reference_api_rejects_non_auditable_review_window() -> None:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    payload = {
        "product_id": str(PRODUCT_ID),
        "purchase_source": "leboncoin",
        "destination_marketplace": "ebay",
        "required_tokens": ["wh-1000xm5"],
        "excluded_tokens": [],
        "condition": "like_new",
        "reviewed_price_eur": "500.00",
        "currency": "EUR",
        "evidence_url": "https://example.com/sold/1",
        "comparable_sold_at": "2026-09-09T12:00:00Z",
        "reviewed_at": "2026-09-08T12:00:00Z",
        "reviewed_by": "operator@example.com",
        "expires_at": "2026-09-07T12:00:00Z",
        "purchase_fee_rate": "0.00",
        "purchase_fee_fixed_eur": "0.00",
        "sell_fee_rate": "0.10",
        "sell_fee_fixed_eur": "0.00",
        "social_charge_rate": "0.123",
        "outbound_logistics_eur": "20.00",
        "risk_allowance_eur": "15.00",
        "minimum_contribution_eur": "20.00",
        "limitations": "Exact model and condition.",
    }

    response = client.post("/valuation/references", json=payload)

    assert response.status_code == 422

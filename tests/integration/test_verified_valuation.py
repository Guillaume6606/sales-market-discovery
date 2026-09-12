from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.routers.valuation import router
from libs.common.db import get_db
from libs.common.models import ListingObservation


def test_verified_reference_and_listing_review_flow(
    integration_db: Session, seed_product: str
) -> None:
    now = datetime.now(UTC)
    observation = ListingObservation(
        product_id=UUID(seed_product),
        source="vinted",
        listing_id="vinted-verified-review",
        title="Sony headset",
        price=Decimal("300.00"),
        currency="EUR",
        condition=None,
        shipping_cost=None,
        is_sold=False,
        is_stale=False,
        evidence_type="asking",
        observed_at=now,
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
        url="https://example.com/listing/vinted-verified-review",
    )
    integration_db.add(observation)
    integration_db.commit()
    integration_db.refresh(observation)

    app = FastAPI()
    app.include_router(router)

    def override_db() -> Generator[Session, None, None]:
        yield integration_db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    reference_response = client.post(
        "/valuation/references",
        json={
            "product_id": seed_product,
            "purchase_source": "vinted",
            "destination_marketplace": "ebay",
            "required_tokens": ["sony", "wh-1000xm5"],
            "excluded_tokens": ["pieces"],
            "condition": "like_new",
            "reviewed_price_eur": "500.00",
            "currency": "EUR",
            "evidence_url": "https://example.com/sold/sony-wh-1000xm5",
            "comparable_sold_at": (now - timedelta(days=4)).isoformat(),
            "reviewed_at": (now - timedelta(minutes=10)).isoformat(),
            "reviewed_by": "operator@example.com",
            "expires_at": (now + timedelta(days=30)).isoformat(),
            "limitations": "Exact model and condition; colour may differ.",
            "purchase_fee_rate": "0.02",
            "purchase_fee_fixed_eur": "5.00",
            "sell_fee_rate": "0.10",
            "sell_fee_fixed_eur": "3.00",
            "social_charge_rate": "0.123",
            "outbound_logistics_eur": "20.00",
            "risk_allowance_eur": "15.00",
            "minimum_contribution_eur": "20.00",
        },
    )
    assert reference_response.status_code == 201

    blocked = client.get(f"/valuation/listings/{observation.obs_id}")
    assert blocked.status_code == 200
    assert blocked.json()["eligible"] is False
    assert "listing_condition_unknown" in blocked.json()["reasons"]
    assert "listing_missing_shipping_cost" in blocked.json()["reasons"]

    review_response = client.patch(
        f"/valuation/listings/{observation.obs_id}/review",
        json={
            "reviewed_title": "Sony WH-1000XM5 headset",
            "reviewed_condition": "like_new",
            "reviewed_shipping_cost_eur": "10.00",
            "reviewed_delivery_to_france": True,
            "delivery_evidence": "Checkout confirms delivery to France address",
            "reviewed_by": "operator@example.com",
            "reviewed_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=2)).isoformat(),
            "notes": "Opened the live listing and verified identity and delivery cost.",
        },
    )
    assert review_response.status_code == 201
    assert review_response.json()["raw_title"] == "Sony headset"
    assert review_response.json()["raw_price_eur"] == "300.00"
    assert review_response.json()["raw_condition"] is None
    assert review_response.json()["raw_shipping_cost_eur"] is None

    eligible = client.get(f"/valuation/listings/{observation.obs_id}")
    assert eligible.status_code == 200
    assert eligible.json()["eligible"] is True
    assert eligible.json()["acquisition_cost_eur"] == "321.00"
    assert eligible.json()["contribution_eur"] == "29.50"

    observation.price = Decimal("290.00")
    integration_db.commit()

    changed = client.get(f"/valuation/listings/{observation.obs_id}")
    assert changed.status_code == 200
    assert changed.json()["eligible"] is False
    assert changed.json()["listing_review_snapshot"] is None
    assert "listing_condition_unknown" in changed.json()["reasons"]

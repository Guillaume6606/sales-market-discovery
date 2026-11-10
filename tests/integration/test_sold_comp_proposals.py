from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.routers.sold_comps import router
from ingestion.connectors.ebay_sold import parse_sold_search_html
from ingestion.sold_comps import build_proposal_drafts, identity_spec_for_product, persist_proposals
from ingestion.valuation import evaluate_valuation
from libs.common.db import get_db
from libs.common.models import ListingObservation, ProductTemplate
from libs.common.sold_comp_models import SoldCompProposal
from libs.common.valuation_models import VerifiedValuationReference

FIXTURE = (
    Path(__file__).parents[1] / "unit" / "fixtures" / "ebay_sold_sony_wh1000xm4.synthetic.html"
)


def _persist(db: Session, product: ProductTemplate, decision_at: datetime) -> SoldCompProposal:
    spec = identity_spec_for_product(product)
    page = parse_sold_search_html(FIXTURE.read_text(encoding="utf-8"))
    sales = [
        sale.__class__(**{**sale.__dict__, "sold_at": decision_at - timedelta(days=index + 1)})
        for index, sale in enumerate(page.sales)
    ]
    drafts, excluded = build_proposal_drafts(
        sales,
        spec,
        decision_at=decision_at,
        window_days=90,
        min_comparables=5,
        excluded_item_ids=set(),
    )
    created = persist_proposals(
        db, product, spec, drafts, excluded, decision_at=decision_at, min_comparables=5
    )
    return next(proposal for proposal in created if proposal.condition == "good")


def test_proposal_requires_approval_before_any_valuation_is_eligible(
    integration_db: Session, seed_product: str
) -> None:
    now = datetime.now(UTC)
    product = integration_db.get(ProductTemplate, UUID(seed_product))
    assert product is not None
    product.search_query = "Sony WH-1000XM4"
    product.words_to_avoid = ["coussinets"]
    integration_db.commit()

    observation = ListingObservation(
        product_id=product.product_id,
        source="leboncoin",
        listing_id="lbc-sold-comps",
        title="Sony WH-1000XM4 casque noir",
        price=Decimal("40.00"),
        currency="EUR",
        condition="Bon état",
        shipping_cost=Decimal("0.00"),
        delivery_to_france=True,
        delivery_evidence="Seller ships with Colissimo to France",
        is_sold=False,
        is_stale=False,
        evidence_type="asking",
        observed_at=now,
        first_seen_at=now,
        last_seen_at=now,
        updated_at=now,
        url="https://example.com/listing/lbc-sold-comps",
    )
    integration_db.add(observation)
    integration_db.commit()

    proposal = _persist(integration_db, product, now - timedelta(minutes=5))
    assert proposal.status == "pending"
    assert proposal.sale_count >= 5

    blocked = evaluate_valuation(integration_db, observation, now=now)
    assert blocked["eligible"] is False
    assert "no_verified_reference" in blocked["reasons"]

    app = FastAPI()
    app.include_router(router)

    def override_db() -> Generator[Session, None, None]:
        yield integration_db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    response = client.post(
        f"/sold-comps/proposals/{proposal.proposal_id}/approve",
        json={
            "purchase_source": "leboncoin",
            "destination_marketplace": "ebay",
            "reviewed_by": "operator@example.com",
            "expires_at": (now + timedelta(days=30)).isoformat(),
            "limitations": "Exact model; colour may differ.",
            "purchase_fee_rate": "0",
            "purchase_fee_fixed_eur": "0",
            "sell_fee_rate": "0.13",
            "sell_fee_fixed_eur": "0.35",
            "social_charge_rate": "0.123",
            "outbound_logistics_eur": "7.00",
            "risk_allowance_eur": "10.00",
            "minimum_contribution_eur": "20.00",
        },
    )
    assert response.status_code == 201, response.text

    integration_db.expire_all()
    reference = integration_db.query(VerifiedValuationReference).one()
    stored = integration_db.get(SoldCompProposal, proposal.proposal_id)
    assert stored is not None and stored.status == "approved"
    assert stored.reference_id == reference.reference_id

    evaluated = evaluate_valuation(integration_db, observation, now=datetime.now(UTC))
    assert evaluated["reference_id"] == str(reference.reference_id)
    assert evaluated["eligible"] is True, evaluated["reasons"]


def test_refresh_supersedes_previous_pending_proposal(
    integration_db: Session, seed_product: str
) -> None:
    now = datetime.now(UTC)
    product = integration_db.get(ProductTemplate, UUID(seed_product))
    assert product is not None
    product.search_query = "Sony WH-1000XM4"
    product.words_to_avoid = []
    integration_db.commit()

    first = _persist(integration_db, product, now - timedelta(days=8))
    second = _persist(integration_db, product, now)

    integration_db.expire_all()
    assert integration_db.get(SoldCompProposal, first.proposal_id).status == "rejected"
    assert integration_db.get(SoldCompProposal, second.proposal_id).status == "pending"

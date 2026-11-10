from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.sold_comps import router
from libs.common.db import get_db
from libs.common.sold_comp_models import SoldCompProposal
from libs.common.valuation_models import VerifiedValuationReference

PRODUCT_ID = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime.now(UTC)


def _proposal(**overrides: Any) -> SoldCompProposal:
    values: dict[str, Any] = {
        "proposal_id": uuid4(),
        "product_id": PRODUCT_ID,
        "source": "ebay_fr_sold_search",
        "condition": "good",
        "status": "pending",
        "search_query": "Sony WH-1000XM4",
        "search_url": "https://www.ebay.fr/sch/i.html?_nkw=Sony+WH-1000XM4&LH_Sold=1&LH_Complete=1",
        "required_tokens": ["Sony", "WH-1000XM4"],
        "excluded_tokens": ["coussinets"],
        "sale_count": 9,
        "min_comparables": 8,
        "median_price_eur": Decimal("142.50"),
        "p25_price_eur": Decimal("137.75"),
        "p75_price_eur": Decimal("147.25"),
        "first_sold_at": NOW - timedelta(days=40),
        "last_sold_at": NOW - timedelta(days=2),
        "computed_at": NOW - timedelta(days=1),
        "sales": [{"item_id": "356001000001", "price_eur": "145.00"}],
        "excluded_sales": [{"item_id": "356001000003", "reason": "best_offer_price_not_final"}],
    }
    values.update(overrides)
    return SoldCompProposal(**values)


class _Query:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def filter(self, *_args: object) -> "_Query":
        return self

    def order_by(self, *_args: object) -> "_Query":
        return self

    def with_for_update(self) -> "_Query":
        return self

    def all(self) -> list[Any]:
        return self.rows

    def one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None


class _FakeDb:
    def __init__(self, proposals: list[SoldCompProposal]) -> None:
        self.proposals = proposals
        self.references: list[VerifiedValuationReference] = []
        self.commits = 0

    def query(self, _model: object) -> _Query:
        return _Query(self.proposals)

    def get(self, _model: object, key: object) -> SoldCompProposal | None:
        return next((p for p in self.proposals if str(p.proposal_id) == str(key)), None)

    def add(self, value: object) -> None:
        if isinstance(value, VerifiedValuationReference):
            self.references.append(value)

    def flush(self) -> None:
        for reference in self.references:
            if reference.reference_id is None:
                reference.reference_id = uuid4()

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, _value: object) -> None:
        return None


def _client(db: _FakeDb) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _approval() -> dict[str, Any]:
    return {
        "purchase_source": "leboncoin",
        "destination_marketplace": "ebay",
        "reviewed_by": "operator@example.com",
        "expires_at": (NOW + timedelta(days=30)).isoformat(),
        "limitations": "Black or silver only; box not required.",
        "purchase_fee_rate": "0",
        "purchase_fee_fixed_eur": "0",
        "sell_fee_rate": "0.13",
        "sell_fee_fixed_eur": "0.35",
        "social_charge_rate": "0.123",
        "outbound_logistics_eur": "7.00",
        "risk_allowance_eur": "10.00",
        "minimum_contribution_eur": "20.00",
    }


def test_detail_exposes_source_sales_for_audit() -> None:
    proposal = _proposal()
    response = _client(_FakeDb([proposal])).get(f"/sold-comps/proposals/{proposal.proposal_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["sales"][0]["item_id"] == "356001000001"
    assert body["excluded_sales"][0]["reason"] == "best_offer_price_not_final"


def test_approval_creates_reference_from_proposal_evidence() -> None:
    proposal = _proposal()
    db = _FakeDb([proposal])

    response = _client(db).post(
        f"/sold-comps/proposals/{proposal.proposal_id}/approve", json=_approval()
    )

    assert response.status_code == 201, response.text
    assert len(db.references) == 1
    reference = db.references[0]
    assert reference.reviewed_price_eur == Decimal("142.50")
    assert reference.condition == "good"
    assert reference.required_tokens == ["sony", "wh", "1000xm4"]
    assert reference.excluded_tokens == ["coussinets"]
    assert reference.purchase_source == "leboncoin"
    assert reference.comparable_sold_at == proposal.last_sold_at
    assert reference.comparable_sold_at <= reference.reviewed_at < reference.expires_at
    assert reference.evidence_url == proposal.search_url
    assert str(proposal.proposal_id) in reference.limitations
    body = response.json()
    assert body["status"] == "approved"
    assert body["reference_id"] == str(reference.reference_id)
    assert db.commits == 1


@pytest.mark.parametrize("status", ["insufficient", "rejected", "approved"])
def test_only_pending_proposals_can_be_approved(status: str) -> None:
    proposal = _proposal(status=status, sale_count=3)
    db = _FakeDb([proposal])

    response = _client(db).post(
        f"/sold-comps/proposals/{proposal.proposal_id}/approve", json=_approval()
    )

    assert response.status_code == 409
    assert db.references == []


def test_approval_requires_operator_expiry_after_review() -> None:
    proposal = _proposal()
    db = _FakeDb([proposal])
    payload = _approval()
    payload["expires_at"] = (NOW - timedelta(days=1)).isoformat()

    response = _client(db).post(
        f"/sold-comps/proposals/{proposal.proposal_id}/approve", json=payload
    )

    assert response.status_code == 422
    assert db.references == []
    assert proposal.status == "pending"


def test_approval_requires_every_cost_input() -> None:
    proposal = _proposal()
    payload = _approval()
    del payload["risk_allowance_eur"]

    response = _client(_FakeDb([proposal])).post(
        f"/sold-comps/proposals/{proposal.proposal_id}/approve", json=payload
    )

    assert response.status_code == 422


def test_rejection_records_reviewer_and_note() -> None:
    proposal = _proposal(status="insufficient", sale_count=3)
    db = _FakeDb([proposal])

    response = _client(db).post(
        f"/sold-comps/proposals/{proposal.proposal_id}/reject",
        json={"reviewed_by": "operator@example.com", "note": "Mixed colour variants"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert proposal.review_note == "Mixed colour variants"
    assert db.references == []


def test_unknown_proposal_returns_404() -> None:
    response = _client(_FakeDb([])).post(
        f"/sold-comps/proposals/{uuid4()}/reject",
        json={"reviewed_by": "operator@example.com", "note": "n/a"},
    )

    assert response.status_code == 404

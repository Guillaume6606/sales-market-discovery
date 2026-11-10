from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from backend.routers.valuation import (
    Marketplace,
    Money,
    PurchaseMarketplace,
    Rate,
    ReferencePayload,
    _payload_values,
)
from libs.common.db import get_db
from libs.common.sold_comp_models import SoldCompProposal
from libs.common.valuation_models import VerifiedValuationReference

router = APIRouter(prefix="/sold-comps", tags=["sold-comps"])

ProposalStatus = Literal["pending", "approved", "rejected", "insufficient"]


class ApprovalPayload(BaseModel):
    purchase_source: PurchaseMarketplace
    destination_marketplace: Marketplace
    reviewed_by: str = Field(min_length=1, max_length=200)
    expires_at: datetime
    limitations: str = Field(min_length=1)
    purchase_fee_rate: Rate
    purchase_fee_fixed_eur: Money
    sell_fee_rate: Rate
    sell_fee_fixed_eur: Money
    social_charge_rate: Rate
    outbound_logistics_eur: Money
    risk_allowance_eur: Money
    minimum_contribution_eur: Money

    @field_validator("reviewed_by", "limitations")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class RejectionPayload(BaseModel):
    reviewed_by: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("reviewed_by", "note")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


def _summary(proposal: SoldCompProposal) -> dict[str, Any]:
    return {
        "proposal_id": str(proposal.proposal_id),
        "product_id": str(proposal.product_id),
        "source": proposal.source,
        "condition": proposal.condition,
        "status": proposal.status,
        "search_query": proposal.search_query,
        "search_url": proposal.search_url,
        "required_tokens": list(proposal.required_tokens or []),
        "excluded_tokens": list(proposal.excluded_tokens or []),
        "sale_count": proposal.sale_count,
        "min_comparables": proposal.min_comparables,
        "median_price_eur": proposal.median_price_eur,
        "p25_price_eur": proposal.p25_price_eur,
        "p75_price_eur": proposal.p75_price_eur,
        "first_sold_at": proposal.first_sold_at,
        "last_sold_at": proposal.last_sold_at,
        "computed_at": proposal.computed_at,
        "reviewed_by": proposal.reviewed_by,
        "reviewed_at": proposal.reviewed_at,
        "review_note": proposal.review_note,
        "reference_id": None if proposal.reference_id is None else str(proposal.reference_id),
    }


def _detail(proposal: SoldCompProposal) -> dict[str, Any]:
    return {
        **_summary(proposal),
        "sales": list(proposal.sales or []),
        "excluded_sales": list(proposal.excluded_sales or []),
    }


def _locked_proposal(db: Session, proposal_id: UUID) -> SoldCompProposal:
    proposal = (
        db.query(SoldCompProposal)
        .filter(SoldCompProposal.proposal_id == proposal_id)
        .with_for_update()
        .one_or_none()
    )
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    return proposal


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.get("/proposals")
def list_proposals(
    status_filter: ProposalStatus | None = Query(None, alias="status"),
    product_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = db.query(SoldCompProposal)
    if status_filter is not None:
        query = query.filter(SoldCompProposal.status == status_filter)
    if product_id is not None:
        query = query.filter(SoldCompProposal.product_id == product_id)
    proposals = query.order_by(
        SoldCompProposal.computed_at.desc(), SoldCompProposal.proposal_id.desc()
    ).all()
    return [_summary(proposal) for proposal in proposals]


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    proposal = db.get(SoldCompProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    return _detail(proposal)


@router.post("/proposals/{proposal_id}/approve", status_code=status.HTTP_201_CREATED)
def approve_proposal(
    proposal_id: UUID,
    payload: ApprovalPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    proposal = _locked_proposal(db, proposal_id)
    if proposal.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Only pending proposals can be approved (status: {proposal.status})",
        )
    if (
        proposal.median_price_eur is None
        or proposal.last_sold_at is None
        or proposal.first_sold_at is None
        or proposal.sale_count < proposal.min_comparables
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Insufficient comparable evidence")

    reviewed_at = datetime.now(UTC)
    first_sold_at = _aware(proposal.first_sold_at)
    last_sold_at = _aware(proposal.last_sold_at)
    evidence = (
        f"eBay.fr completed sales: median of {proposal.sale_count} item prices excluding "
        f"shipping, sold {first_sold_at:%Y-%m-%d} to {last_sold_at:%Y-%m-%d}, "
        f"IQR {proposal.p25_price_eur}-{proposal.p75_price_eur} EUR; "
        f"sold-comp proposal {proposal.proposal_id}."
    )
    try:
        reference_payload = ReferencePayload(
            product_id=proposal.product_id,
            purchase_source=payload.purchase_source,
            destination_marketplace=payload.destination_marketplace,
            required_tokens=list(proposal.required_tokens or []),
            excluded_tokens=list(proposal.excluded_tokens or []),
            condition=proposal.condition,
            reviewed_price_eur=proposal.median_price_eur,
            currency="EUR",
            evidence_url=proposal.search_url,
            comparable_sold_at=last_sold_at,
            reviewed_at=reviewed_at,
            reviewed_by=payload.reviewed_by,
            expires_at=payload.expires_at,
            limitations=f"{payload.limitations} | {evidence}",
            purchase_fee_rate=payload.purchase_fee_rate,
            purchase_fee_fixed_eur=payload.purchase_fee_fixed_eur,
            sell_fee_rate=payload.sell_fee_rate,
            sell_fee_fixed_eur=payload.sell_fee_fixed_eur,
            social_charge_rate=payload.social_charge_rate,
            outbound_logistics_eur=payload.outbound_logistics_eur,
            risk_allowance_eur=payload.risk_allowance_eur,
            minimum_contribution_eur=payload.minimum_contribution_eur,
        )
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc

    reference = VerifiedValuationReference(**_payload_values(reference_payload))
    db.add(reference)
    db.flush()
    proposal.status = "approved"
    proposal.reference_id = reference.reference_id
    proposal.reviewed_by = payload.reviewed_by
    proposal.reviewed_at = reviewed_at
    proposal.review_note = None
    db.commit()
    db.refresh(proposal)
    return _detail(proposal)


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: UUID,
    payload: RejectionPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    proposal = _locked_proposal(db, proposal_id)
    if proposal.status not in {"pending", "insufficient"}:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Proposal is already {proposal.status}",
        )
    proposal.status = "rejected"
    proposal.reviewed_by = payload.reviewed_by
    proposal.reviewed_at = datetime.now(UTC)
    proposal.review_note = payload.note
    db.commit()
    db.refresh(proposal)
    return _detail(proposal)

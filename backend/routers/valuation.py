import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from ingestion.valuation import evaluate_valuation
from libs.common.db import get_db
from libs.common.models import ListingObservation, ProductTemplate
from libs.common.valuation_models import ValuationListingReview, VerifiedValuationReference

router = APIRouter(prefix="/valuation", tags=["valuation"])

Marketplace = Literal["ebay", "leboncoin", "vinted"]
PurchaseMarketplace = Literal["ebay", "leboncoin", "vinted", "cashconverters"]
Condition = Literal["new", "like_new", "good", "fair"]
Money = Annotated[Decimal, Field(ge=Decimal("0"), max_digits=12, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=Decimal("0"), max_digits=12, decimal_places=2)]
Rate = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"), max_digits=8)]


def _normalize_tokens(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
        normalized.extend(re.findall(r"[a-z0-9]+", without_accents))
    return list(dict.fromkeys(normalized))


class ReferencePayload(BaseModel):
    product_id: UUID
    purchase_source: PurchaseMarketplace
    destination_marketplace: Marketplace
    required_tokens: list[str] = Field(min_length=1)
    excluded_tokens: list[str]
    condition: Condition
    reviewed_price_eur: PositiveMoney
    currency: Literal["EUR"]
    evidence_url: AnyHttpUrl
    comparable_sold_at: datetime
    reviewed_at: datetime
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
    is_active: bool = True

    @field_validator("required_tokens", "excluded_tokens")
    @classmethod
    def normalize_tokens(cls, values: list[str]) -> list[str]:
        return _normalize_tokens(values)

    @field_validator("reviewed_by", "limitations")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("comparable_sold_at", "reviewed_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_reference(self) -> "ReferencePayload":
        if not self.required_tokens:
            raise ValueError("required_tokens must contain at least one word token")
        if set(self.required_tokens).intersection(self.excluded_tokens):
            raise ValueError("required_tokens and excluded_tokens must not overlap")
        if self.comparable_sold_at > self.reviewed_at:
            raise ValueError("comparable_sold_at must be on or before reviewed_at")
        if self.reviewed_at >= self.expires_at:
            raise ValueError("expires_at must be after reviewed_at")
        if self.reviewed_at > datetime.now(UTC):
            raise ValueError("reviewed_at must not be in the future")
        return self


class ListingReviewPayload(BaseModel):
    reviewed_title: str = Field(min_length=1)
    reviewed_condition: Condition
    reviewed_shipping_cost_eur: Money
    reviewed_delivery_to_france: bool | None = None
    delivery_evidence: str | None = Field(default=None, max_length=2000)
    reviewed_by: str = Field(min_length=1, max_length=200)
    reviewed_at: datetime
    expires_at: datetime
    notes: str = Field(min_length=1)

    @field_validator("reviewed_title", "reviewed_by", "notes")
    @classmethod
    def strip_review_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("reviewed_at", "expires_at")
    @classmethod
    def require_review_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_review_window(self) -> "ListingReviewPayload":
        if (
            self.reviewed_delivery_to_france is not None
            and not (self.delivery_evidence or "").strip()
        ):
            raise ValueError("delivery_evidence is required for a delivery decision")
        if self.reviewed_at >= self.expires_at:
            raise ValueError("expires_at must be after reviewed_at")
        if self.reviewed_at > datetime.now(UTC):
            raise ValueError("reviewed_at must not be in the future")
        return self


def _payload_values(payload: ReferencePayload) -> dict[str, Any]:
    values = payload.model_dump()
    values["evidence_url"] = str(payload.evidence_url)
    return values


def _serialize(reference: VerifiedValuationReference) -> dict[str, Any]:
    return {
        "reference_id": str(reference.reference_id),
        "product_id": str(reference.product_id),
        "purchase_source": reference.purchase_source,
        "destination_marketplace": reference.destination_marketplace,
        "required_tokens": list(reference.required_tokens or []),
        "excluded_tokens": list(reference.excluded_tokens or []),
        "condition": reference.condition,
        "reviewed_price_eur": reference.reviewed_price_eur,
        "currency": reference.currency,
        "evidence_url": reference.evidence_url,
        "comparable_sold_at": reference.comparable_sold_at,
        "reviewed_at": reference.reviewed_at,
        "reviewed_by": reference.reviewed_by,
        "expires_at": reference.expires_at,
        "limitations": reference.limitations,
        "purchase_fee_rate": reference.purchase_fee_rate,
        "purchase_fee_fixed_eur": reference.purchase_fee_fixed_eur,
        "sell_fee_rate": reference.sell_fee_rate,
        "sell_fee_fixed_eur": reference.sell_fee_fixed_eur,
        "social_charge_rate": reference.social_charge_rate,
        "outbound_logistics_eur": reference.outbound_logistics_eur,
        "risk_allowance_eur": reference.risk_allowance_eur,
        "minimum_contribution_eur": reference.minimum_contribution_eur,
        "is_active": reference.is_active,
        "created_at": reference.created_at,
        "updated_at": reference.updated_at,
    }


def _serialize_listing_review(review: ValuationListingReview) -> dict[str, Any]:
    return {
        "review_id": str(review.review_id),
        "reviewed_delivery_to_france": review.reviewed_delivery_to_france,
        "delivery_evidence": review.delivery_evidence,
        "obs_id": review.obs_id,
        "reviewed_title": review.reviewed_title,
        "reviewed_condition": review.reviewed_condition,
        "reviewed_shipping_cost_eur": review.reviewed_shipping_cost_eur,
        "reviewed_by": review.reviewed_by,
        "reviewed_at": review.reviewed_at,
        "expires_at": review.expires_at,
        "notes": review.notes,
        "raw_title": review.raw_title,
        "raw_price_eur": review.raw_price_eur,
        "raw_condition": review.raw_condition,
        "raw_shipping_cost_eur": review.raw_shipping_cost_eur,
        "is_active": review.is_active,
        "created_at": review.created_at,
    }


def _get_reference(db: Session, reference_id: UUID) -> VerifiedValuationReference:
    reference = db.get(VerifiedValuationReference, reference_id)
    if reference is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Valuation reference not found")
    return reference


@router.post("/references", status_code=status.HTTP_201_CREATED)
def create_reference(payload: ReferencePayload, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(ProductTemplate, payload.product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    reference = VerifiedValuationReference(**_payload_values(payload))
    db.add(reference)
    db.commit()
    db.refresh(reference)
    return _serialize(reference)


@router.get("/references")
def list_references(
    product_id: UUID | None = None,
    purchase_source: PurchaseMarketplace | None = None,
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = db.query(VerifiedValuationReference)
    if product_id is not None:
        query = query.filter(VerifiedValuationReference.product_id == product_id)
    if purchase_source is not None:
        query = query.filter(VerifiedValuationReference.purchase_source == purchase_source)
    if not include_inactive:
        query = query.filter(VerifiedValuationReference.is_active.is_(True))
    references = query.order_by(
        VerifiedValuationReference.reviewed_at.desc(),
        VerifiedValuationReference.reference_id.desc(),
    ).all()
    return [_serialize(reference) for reference in references]


@router.get("/references/{reference_id}")
def get_reference(reference_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _serialize(_get_reference(db, reference_id))


@router.put("/references/{reference_id}")
def update_reference(
    reference_id: UUID,
    payload: ReferencePayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    reference = _get_reference(db, reference_id)
    if db.get(ProductTemplate, payload.product_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    for field, value in _payload_values(payload).items():
        setattr(reference, field, value)
    db.commit()
    db.refresh(reference)
    return _serialize(reference)


@router.delete("/references/{reference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reference(reference_id: UUID, db: Session = Depends(get_db)) -> Response:
    reference = _get_reference(db, reference_id)
    reference.is_active = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/listings/{obs_id}")
def evaluate_listing(obs_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    observation = db.get(ListingObservation, obs_id)
    if observation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Listing observation not found")
    return evaluate_valuation(db, observation)


@router.patch(
    "/listings/{obs_id}/review",
    status_code=status.HTTP_201_CREATED,
)
def review_listing(
    obs_id: int,
    payload: ListingReviewPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    observation = db.get(ListingObservation, obs_id)
    if observation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Listing observation not found")
    listing_url = str(observation.url or "").strip()
    parsed_url = urlparse(listing_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Listing must have an http(s) URL before review",
        )
    try:
        raw_price = Decimal(str(observation.price))
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Listing must have a valid positive price before review",
        ) from exc
    if not raw_price.is_finite() or raw_price <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Listing must have a valid positive price before review",
        )
    review = ValuationListingReview(
        obs_id=observation.obs_id,
        reviewed_title=payload.reviewed_title,
        reviewed_condition=payload.reviewed_condition,
        reviewed_shipping_cost_eur=payload.reviewed_shipping_cost_eur,
        reviewed_delivery_to_france=payload.reviewed_delivery_to_france,
        delivery_evidence=payload.delivery_evidence,
        raw_delivery_to_france=observation.delivery_to_france,
        raw_delivery_evidence=observation.delivery_evidence,
        raw_url=observation.url,
        reviewed_by=payload.reviewed_by,
        reviewed_at=payload.reviewed_at,
        expires_at=payload.expires_at,
        notes=payload.notes,
        raw_title=observation.title,
        raw_price_eur=raw_price,
        raw_condition=observation.condition,
        raw_shipping_cost_eur=observation.shipping_cost,
        is_active=True,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return _serialize_listing_review(review)


@router.get("/listings/{obs_id}/reviews")
def list_listing_reviews(obs_id: int, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    if db.get(ListingObservation, obs_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Listing observation not found")
    reviews = (
        db.query(ValuationListingReview)
        .filter(ValuationListingReview.obs_id == obs_id)
        .order_by(
            ValuationListingReview.reviewed_at.desc(),
            ValuationListingReview.review_id.desc(),
        )
        .all()
    )
    return [_serialize_listing_review(review) for review in reviews]

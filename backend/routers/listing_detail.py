"""Listing detail endpoint — full joined view of a single listing observation."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from libs.common.db import get_db
from libs.common.models import (
    ListingDetailORM,
    ListingEnrichment,
    ListingObservation,
    ListingScore,
    MarketPriceNormal,
    ProductTemplate,
)
from libs.common.utils import decimal_to_float as _decimal_to_float

router = APIRouter(tags=["listing_detail"])


@router.get("/listings/{obs_id}/detail")
def get_listing_detail(obs_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return full listing detail across all enrichment tables.

    Args:
        obs_id: Primary key of the listing observation to retrieve.
        db: Database session injected by FastAPI.

    Returns:
        Dict with keys ``observation``, ``detail``, ``enrichment``, ``score``,
        and ``pmn``.  Any section is ``null`` when the corresponding row does
        not exist.

    Raises:
        HTTPException: 404 when no listing observation matches ``obs_id``.
    """
    row = (
        db.query(
            ListingObservation,
            ProductTemplate,
            ListingDetailORM,
            ListingEnrichment,
            ListingScore,
        )
        .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
        .outerjoin(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
        .outerjoin(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
        .outerjoin(ListingScore, ListingScore.obs_id == ListingObservation.obs_id)
        .filter(ListingObservation.obs_id == obs_id)
        .first()
    )

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")

    obs, product, detail, enrichment, score = row

    # Fetch PMN via product_id (one row per product)
    pmn = db.query(MarketPriceNormal).filter(MarketPriceNormal.product_id == obs.product_id).first()

    return {
        "observation": {
            "obs_id": obs.obs_id,
            "product_id": str(obs.product_id),
            "product_name": product.name,
            "product_brand": product.brand,
            "category": str(product.category_id),
            "source": obs.source,
            "listing_id": obs.listing_id,
            "title": obs.title,
            "price": _decimal_to_float(obs.price),
            "currency": obs.currency,
            "condition": obs.condition,
            "is_sold": obs.is_sold,
            "seller_rating": _decimal_to_float(obs.seller_rating),
            "shipping_cost": _decimal_to_float(obs.shipping_cost),
            "location": obs.location,
            "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
            "url": obs.url,
            "last_seen_at": obs.last_seen_at.isoformat() if obs.last_seen_at else None,
            "is_stale": obs.is_stale,
            "llm_validated": obs.llm_validated,
            "llm_validation_result": obs.llm_validation_result,
        },
        "detail": {
            "description": detail.description,
            "description_length": detail.description_length,
            "photo_urls": detail.photo_urls,
            "photo_count": detail.photo_count,
            "local_pickup_only": detail.local_pickup_only,
            "negotiation_enabled": detail.negotiation_enabled,
            "original_posted_at": detail.original_posted_at.isoformat()
            if detail.original_posted_at
            else None,
            "seller_account_age_days": detail.seller_account_age_days,
            "seller_transaction_count": detail.seller_transaction_count,
            "view_count": detail.view_count,
            "favorite_count": detail.favorite_count,
            "fetched_at": detail.fetched_at.isoformat() if detail.fetched_at else None,
        }
        if detail is not None
        else None,
        "enrichment": {
            "urgency_score": _decimal_to_float(enrichment.urgency_score),
            "urgency_keywords": enrichment.urgency_keywords,
            "has_original_box": enrichment.has_original_box,
            "has_receipt_or_invoice": enrichment.has_receipt_or_invoice,
            "accessories_included": enrichment.accessories_included,
            "accessories_completeness": _decimal_to_float(enrichment.accessories_completeness),
            "photo_quality_score": _decimal_to_float(enrichment.photo_quality_score),
            "listing_quality_score": _decimal_to_float(enrichment.listing_quality_score),
            "condition_confidence": _decimal_to_float(enrichment.condition_confidence),
            "fakeness_probability": _decimal_to_float(enrichment.fakeness_probability),
            "seller_motivation_score": _decimal_to_float(enrichment.seller_motivation_score),
            "enriched_at": enrichment.enriched_at.isoformat() if enrichment.enriched_at else None,
        }
        if enrichment is not None
        else None,
        "score": {
            "arbitrage_spread_eur": _decimal_to_float(score.arbitrage_spread_eur),
            "net_roi_pct": _decimal_to_float(score.net_roi_pct),
            "risk_adjusted_confidence": _decimal_to_float(score.risk_adjusted_confidence),
            "acquisition_cost_eur": _decimal_to_float(score.acquisition_cost_eur),
            "estimated_sale_price_eur": _decimal_to_float(score.estimated_sale_price_eur),
            "estimated_sell_fees_eur": _decimal_to_float(score.estimated_sell_fees_eur),
            "estimated_sell_shipping_eur": _decimal_to_float(score.estimated_sell_shipping_eur),
            "days_on_market": score.days_on_market,
            "score_breakdown": score.score_breakdown,
            "scored_at": score.scored_at.isoformat() if score.scored_at else None,
        }
        if score is not None
        else None,
        "pmn": {
            "pmn": _decimal_to_float(pmn.pmn),
            "pmn_low": _decimal_to_float(pmn.pmn_low),
            "pmn_high": _decimal_to_float(pmn.pmn_high),
            "confidence": _decimal_to_float(pmn.confidence),
            "last_computed_at": pmn.last_computed_at.isoformat() if pmn.last_computed_at else None,
        }
        if pmn is not None
        else None,
    }

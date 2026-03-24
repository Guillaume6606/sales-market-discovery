"""Scored listings endpoints — composite arbitrage scores per product."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from libs.common.db import get_db
from libs.common.models import (
    ListingDetailORM,
    ListingEnrichment,
    ListingObservation,
    ListingScore,
)

router = APIRouter(tags=["scored_listings"])


@router.get("/products/{product_id}/scored-listings")
def scored_listings(
    product_id: str,
    min_confidence: float = 80.0,
    sort_by: str = "spread",
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return scored listings for a product filtered by minimum confidence.

    Args:
        product_id: UUID of the product template to query.
        min_confidence: Minimum ``risk_adjusted_confidence`` (0-100). Defaults
            to 80.0.
        sort_by: Sort field — one of ``"spread"``, ``"roi"``, or
            ``"confidence"``. Defaults to ``"spread"``.
        limit: Maximum number of results to return. Defaults to 50.
        db: Database session injected by FastAPI.

    Returns:
        List of dicts combining observation, score, detail, and enrichment
        fields for each qualifying listing.
    """
    sort_column = {
        "spread": ListingScore.arbitrage_spread_eur.desc(),
        "roi": ListingScore.net_roi_pct.desc(),
        "confidence": ListingScore.risk_adjusted_confidence.desc(),
    }.get(sort_by, ListingScore.arbitrage_spread_eur.desc())

    rows = (
        db.query(ListingObservation, ListingScore, ListingDetailORM, ListingEnrichment)
        .join(ListingScore, ListingScore.obs_id == ListingObservation.obs_id)
        .outerjoin(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
        .outerjoin(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
        .filter(
            ListingScore.product_id == product_id,
            ListingScore.risk_adjusted_confidence >= min_confidence,
            ListingObservation.is_stale == False,  # noqa: E712
        )
        .order_by(sort_column)
        .limit(limit)
        .all()
    )

    return [
        {
            "obs_id": obs.obs_id,
            "title": obs.title,
            "price": float(obs.price) if obs.price else None,
            "source": obs.source,
            "url": obs.url,
            "condition": obs.condition,
            "arbitrage_spread_eur": float(score.arbitrage_spread_eur)
            if score.arbitrage_spread_eur
            else None,
            "net_roi_pct": float(score.net_roi_pct) if score.net_roi_pct else None,
            "risk_adjusted_confidence": float(score.risk_adjusted_confidence)
            if score.risk_adjusted_confidence
            else None,
            "acquisition_cost_eur": float(score.acquisition_cost_eur)
            if score.acquisition_cost_eur
            else None,
            "estimated_sale_price_eur": float(score.estimated_sale_price_eur)
            if score.estimated_sale_price_eur
            else None,
            "days_on_market": score.days_on_market,
            "score_breakdown": score.score_breakdown,
            "photo_count": detail.photo_count if detail else None,
            "local_pickup_only": detail.local_pickup_only if detail else None,
            "negotiation_enabled": detail.negotiation_enabled if detail else None,
            "view_count": detail.view_count if detail else None,
            "favorite_count": detail.favorite_count if detail else None,
            "urgency_score": float(enrichment.urgency_score)
            if enrichment and enrichment.urgency_score
            else None,
            "seller_motivation_score": float(enrichment.seller_motivation_score)
            if enrichment and enrichment.seller_motivation_score
            else None,
            "has_original_box": enrichment.has_original_box if enrichment else None,
            "listing_quality_score": float(enrichment.listing_quality_score)
            if enrichment and enrichment.listing_quality_score
            else None,
        }
        for obs, score, detail, enrichment in rows
    ]

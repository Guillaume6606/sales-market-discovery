"""Scored listings endpoints — composite arbitrage scores per product."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.valuation_view import current_score
from ingestion.valuation import evaluate_valuation
from libs.common.db import get_db
from libs.common.models import (
    ListingDetailORM,
    ListingEnrichment,
    ListingObservation,
    ListingScore,
)
from libs.common.settings import settings

router = APIRouter(tags=["scored_listings"])


@router.get("/products/{product_id}/scored-listings")
def scored_listings(
    product_id: str,
    min_confidence: float = 0.0,
    sort_by: str = "spread",
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return scored listings for a product filtered by minimum confidence.

    Args:
        product_id: UUID of the product template to query.
        min_confidence: Minimum ``risk_adjusted_confidence`` (0-100). Defaults
            to 0.0; this heuristic is uncalibrated.
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
            ListingObservation.is_sold.is_(False),
            ListingObservation.last_seen_at
            >= datetime.now(UTC) - timedelta(minutes=settings.alert_freshness_minutes),
            ListingObservation.is_stale == False,  # noqa: E712
        )
        .order_by(sort_column)
        .all()
    )

    result = []
    for obs, score, detail, enrichment in rows:
        valuation = evaluate_valuation(db, obs)
        if not valuation["eligible"]:
            continue
        result.append(
            {
                "obs_id": obs.obs_id,
                "title": obs.title,
                "price": float(obs.price) if obs.price else None,
                "source": obs.source,
                "url": obs.url,
                "condition": obs.condition,
                **current_score(valuation, score),
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
        )
    current_key = {
        "spread": "arbitrage_spread_eur",
        "roi": "net_roi_pct",
        "confidence": "risk_adjusted_confidence",
    }.get(sort_by, "arbitrage_spread_eur")
    result.sort(
        key=lambda row: row[current_key] if row[current_key] is not None else float("-inf"),
        reverse=True,
    )
    return result[:limit]

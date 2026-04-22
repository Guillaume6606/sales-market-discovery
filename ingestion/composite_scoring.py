"""Composite action score computation for arbitrage opportunities."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from libs.common.condition import normalize_condition
from libs.common.settings import settings

logger = logging.getLogger(__name__)

# Fee rates applied when selling on each platform
PLATFORM_SELL_FEES: dict[str, Decimal] = {
    "ebay": Decimal("0.159"),
    "leboncoin": Decimal("0.08"),
    "vinted": Decimal("0.08"),
}

# Fee rates applied when *buying* on each platform (charged to the buyer)
PLATFORM_BUYER_FEES: dict[str, Decimal] = {
    "ebay": Decimal("0"),
    "leboncoin": Decimal("0"),
    "vinted": Decimal("0.05"),
}

# Multiplicative price adjustment per normalised condition tier
CONDITION_ADJUSTMENTS: dict[str | None, Decimal] = {
    "new": Decimal("1.10"),
    "like_new": Decimal("1.00"),
    "good": Decimal("0.90"),
    "fair": Decimal("0.75"),
    None: Decimal("0.90"),  # unknown condition defaults to "good" tier
}

# Estimated outbound shipping cost (EUR) when re-selling, keyed by category slug
SELL_SHIPPING: dict[str, float] = {
    "electronics": settings.scoring_sell_shipping_electronics,
    "watches": settings.scoring_sell_shipping_watches,
    "clothing": settings.scoring_sell_shipping_clothing,
}

# Weighted factors for the risk-adjusted confidence score; must sum to 1.0
CONFIDENCE_WEIGHTS: dict[str, float] = {
    "seller_trust": 0.20,
    "fakeness_inverse": 0.25,
    "condition_confidence": 0.15,
    "pmn_confidence": 0.20,
    "price_volatility_inverse": 0.10,
    "listing_quality": 0.10,
}


# ---------------------------------------------------------------------------
# Pure computation helpers
# ---------------------------------------------------------------------------


def compute_acquisition_cost(
    price: Decimal,
    shipping_cost: Decimal | None,
    source: str,
    local_pickup_only: bool | None,
) -> Decimal:
    """Return the all-in cost of acquiring a listing.

    Args:
        price: Listed price in EUR.
        shipping_cost: Shipping cost in EUR, or ``None`` when unknown.
        source: Marketplace source identifier (``"ebay"``, ``"leboncoin"``, …).
        local_pickup_only: When ``True`` the item requires in-person collection
            so shipping cost is not added.

    Returns:
        Total acquisition cost including shipping and any platform buyer fee.
    """
    cost = price
    if not local_pickup_only and shipping_cost:
        cost += shipping_cost
    buyer_fee_rate = PLATFORM_BUYER_FEES.get(source, Decimal("0"))
    cost += price * buyer_fee_rate
    return cost


def compute_estimated_sale_price(
    pmn: Decimal | None,
    condition_norm: str | None,
    has_box: bool,
    has_receipt: bool,
    full_accessories: bool,
) -> Decimal | None:
    """Estimate the achievable resale price based on PMN and listing quality.

    Args:
        pmn: Price of Market Normal for this product in EUR, or ``None`` when
            not yet computed.
        condition_norm: Normalised condition tier (``"new"``, ``"like_new"``,
            ``"good"``, ``"fair"``, or ``None``).
        has_box: Whether the original box is included.
        has_receipt: Whether a receipt or invoice is included.
        full_accessories: Whether accessories are substantially complete
            (≥80 % completeness score).

    Returns:
        Estimated sale price rounded to the nearest cent, or ``None`` when
        *pmn* is ``None``.
    """
    if pmn is None:
        return None
    adjustment = CONDITION_ADJUSTMENTS.get(condition_norm, CONDITION_ADJUSTMENTS[None])
    price = pmn * adjustment
    if has_box:
        price *= Decimal("1.05")
    if has_receipt:
        price *= Decimal("1.05")
    if full_accessories:
        price *= Decimal("1.05")
    return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_sell_fees(estimated_sale_price: Decimal, source: str) -> Decimal:
    """Compute platform selling fees for the estimated sale price.

    Args:
        estimated_sale_price: Estimated achievable resale price in EUR.
        source: The platform on which the item would be re-listed.

    Returns:
        Fee amount in EUR rounded to the nearest cent.
    """
    rate = PLATFORM_SELL_FEES.get(source, Decimal("0.10"))
    return (estimated_sale_price * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_sell_shipping_estimate(category: str | None) -> Decimal:
    """Return the estimated outbound shipping cost for a given category.

    Args:
        category: Product category slug (e.g. ``"electronics"``), or ``None``.

    Returns:
        Shipping estimate in EUR as a ``Decimal``.
    """
    cat = (category or "").lower()
    cost = SELL_SHIPPING.get(cat, settings.scoring_sell_shipping_default)
    return Decimal(str(cost))


def compute_arbitrage_spread(
    estimated_sale_price: Decimal,
    sell_fees: Decimal,
    sell_shipping: Decimal,
    acquisition_cost: Decimal,
) -> Decimal:
    """Compute the gross arbitrage spread before tax.

    Args:
        estimated_sale_price: Projected resale price in EUR.
        sell_fees: Platform selling fees in EUR.
        sell_shipping: Estimated outbound shipping cost in EUR.
        acquisition_cost: All-in cost to acquire the listing in EUR.

    Returns:
        Spread in EUR (can be negative if the opportunity is loss-making).
    """
    return estimated_sale_price - sell_fees - sell_shipping - acquisition_cost


def compute_net_roi(spread: Decimal, acquisition_cost: Decimal) -> Decimal | None:
    """Compute the net return on investment as a percentage.

    Args:
        spread: Arbitrage spread in EUR (from :func:`compute_arbitrage_spread`).
        acquisition_cost: All-in acquisition cost in EUR.

    Returns:
        ROI percentage rounded to two decimal places, or ``None`` when
        *acquisition_cost* is zero (undefined division).
    """
    if acquisition_cost == 0:
        return None
    return ((spread / acquisition_cost) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_risk_adjusted_confidence(
    seller_trust: float,
    fakeness_probability: float,
    condition_confidence: float,
    pmn_confidence: float | None,
    price_volatility_ratio: float,
    listing_quality: float,
) -> float:
    """Return a 0-100 risk-adjusted confidence score for an opportunity.

    Each factor is clamped to [0, 1] before weighting. When *pmn_confidence*
    is ``None`` (PMN not computed), the score is capped at 40 to reflect the
    high uncertainty of the opportunity.

    Args:
        seller_trust: Trust signal for the seller in [0, 1].
        fakeness_probability: Probability that the listing is counterfeit/fake
            in [0, 1] — higher is worse.
        condition_confidence: Confidence in the condition assessment in [0, 1].
        pmn_confidence: Confidence in the computed PMN value in [0, 1], or
            ``None`` when PMN is unavailable.
        price_volatility_ratio: Price std-dev / PMN ratio in [0, 1] — higher
            means more volatile and therefore riskier.
        listing_quality: Overall listing quality score in [0, 1].

    Returns:
        Score in [0.0, 100.0], rounded to two decimal places.
    """
    no_pmn = pmn_confidence is None
    pmn_conf = pmn_confidence if pmn_confidence is not None else 0.5

    factors = {
        "seller_trust": max(0.0, min(1.0, seller_trust)),
        "fakeness_inverse": max(0.0, min(1.0, 1.0 - fakeness_probability)),
        "condition_confidence": max(0.0, min(1.0, condition_confidence)),
        "pmn_confidence": max(0.0, min(1.0, pmn_conf)),
        "price_volatility_inverse": max(0.0, min(1.0, 1.0 - price_volatility_ratio)),
        "listing_quality": max(0.0, min(1.0, listing_quality)),
    }

    score = sum(factors[k] * CONFIDENCE_WEIGHTS[k] for k in CONFIDENCE_WEIGHTS) * 100
    if no_pmn:
        score = min(score, 40.0)
    return round(score, 2)


# ---------------------------------------------------------------------------
# Orchestration helper — builds the full score dict for a single listing row
# ---------------------------------------------------------------------------


def compute_all_scores(
    obs: Any,
    detail: Any | None,
    enrichment: Any | None,
    pmn_row: Any | None,
    metrics: Any | None,
    product: Any,
) -> dict[str, Any]:
    """Compute all composite scores for a single listing observation row.

    Accepts SQLAlchemy ORM row objects directly.  All optional data sources
    (detail, enrichment, PMN, metrics) may be ``None``; the function degrades
    gracefully using sensible defaults.

    Args:
        obs: ``ListingObservation`` ORM instance.
        detail: ``ListingDetailORM`` ORM instance or ``None``.
        enrichment: ``ListingEnrichment`` ORM instance or ``None``.
        pmn_row: ``MarketPriceNormal`` ORM instance or ``None``.
        metrics: ``ProductDailyMetrics`` ORM instance or ``None``.
        product: ``ProductTemplate`` ORM instance (required).

    Returns:
        Dictionary suitable for direct upsert into ``listing_score``.
    """
    source = obs.source
    condition_norm = normalize_condition(obs.condition)
    category_name = product.category.name if product.category else None

    # --- Accessory / quality flags from enrichment ---
    has_box = enrichment.has_original_box if enrichment else False
    has_receipt = enrichment.has_receipt_or_invoice if enrichment else False
    accessories_complete = (
        (
            enrichment.accessories_completeness is not None
            and float(enrichment.accessories_completeness) >= 0.8
        )
        if enrichment
        else False
    )

    # --- Acquisition cost ---
    local_pickup = detail.local_pickup_only if detail else None
    acquisition_cost = compute_acquisition_cost(
        obs.price or Decimal("0"), obs.shipping_cost, source, local_pickup
    )

    # --- Sale price estimate ---
    pmn_value: Decimal | None = pmn_row.pmn if pmn_row and pmn_row.pmn else None
    estimated_sale_price = compute_estimated_sale_price(
        pmn_value, condition_norm, has_box, has_receipt, accessories_complete
    )

    # --- Fees, shipping, spread, ROI ---
    sell_fees: Decimal | None = None
    spread: Decimal | None = None
    roi: Decimal | None = None
    sell_shipping: Decimal = get_sell_shipping_estimate(category_name)
    if estimated_sale_price:
        sell_fees = compute_sell_fees(estimated_sale_price, source)
        spread = compute_arbitrage_spread(
            estimated_sale_price, sell_fees, sell_shipping, acquisition_cost
        )
        roi = compute_net_roi(spread, acquisition_cost)

    # --- Days on market ---
    dom: int | None = None
    if detail and detail.original_posted_at:
        dom = (datetime.now(UTC) - detail.original_posted_at).days
    elif obs.observed_at:
        dom = (datetime.now(UTC) - obs.observed_at).days

    # --- Seller trust signal ---
    seller_trust = 0.5
    if obs.seller_rating is not None:
        seller_trust = min(float(obs.seller_rating) / 5.0, 1.0)
    if detail and detail.seller_transaction_count is not None:
        tx_signal = min(float(detail.seller_transaction_count) / 100.0, 1.0)
        seller_trust = (seller_trust + tx_signal) / 2.0
    if detail and detail.seller_account_age_days is not None:
        age_signal = min(float(detail.seller_account_age_days) / 365.0, 1.0)
        seller_trust = (seller_trust * 2 + age_signal) / 3.0

    # --- LLM-derived enrichment signals ---
    fakeness_prob = (
        float(enrichment.fakeness_probability)
        if enrichment and enrichment.fakeness_probability is not None
        else 0.5
    )
    cond_conf = (
        float(enrichment.condition_confidence)
        if enrichment and enrichment.condition_confidence is not None
        else 0.5
    )
    pmn_conf: float | None = (
        float(pmn_row.confidence) if pmn_row and pmn_row.confidence is not None else None
    )

    # --- Price volatility ---
    volatility_ratio = 0.5
    if metrics and metrics.price_std and pmn_value:
        volatility_ratio = min(float(metrics.price_std) / float(pmn_value), 1.0)

    listing_qual = (
        float(enrichment.listing_quality_score)
        if enrichment and enrichment.listing_quality_score is not None
        else 0.5
    )

    # --- Composite confidence ---
    confidence = compute_risk_adjusted_confidence(
        seller_trust,
        fakeness_prob,
        cond_conf,
        pmn_conf,
        volatility_ratio,
        listing_qual,
    )

    # --- Score breakdown (for debugging / UI) ---
    breakdown: dict[str, Any] = {
        "acquisition_cost": {
            "price": str(obs.price),
            "shipping": str(obs.shipping_cost),
            "buyer_fee": str(PLATFORM_BUYER_FEES.get(source, 0)),
            "local_pickup": local_pickup,
        },
        "sale_estimate": {
            "pmn": str(pmn_value) if pmn_value else None,
            "condition": condition_norm,
            "condition_adj": str(
                CONDITION_ADJUSTMENTS.get(condition_norm, CONDITION_ADJUSTMENTS[None])
            ),
            "has_box": has_box,
            "has_receipt": has_receipt,
            "full_accessories": accessories_complete,
        },
        "confidence_factors": {
            "seller_trust": round(seller_trust, 3),
            "fakeness_inverse": round(1.0 - fakeness_prob, 3),
            "condition_confidence": round(cond_conf, 3),
            "pmn_confidence": round(pmn_conf, 3) if pmn_conf is not None else None,
            "price_volatility_inverse": round(1.0 - volatility_ratio, 3),
            "listing_quality": round(listing_qual, 3),
        },
    }

    return {
        "obs_id": obs.obs_id,
        "product_id": obs.product_id,
        "arbitrage_spread_eur": spread,
        "net_roi_pct": roi,
        "risk_adjusted_confidence": Decimal(str(confidence)),
        "acquisition_cost_eur": acquisition_cost,
        "estimated_sale_price_eur": estimated_sale_price,
        "estimated_sell_fees_eur": sell_fees,
        "estimated_sell_shipping_eur": sell_shipping,
        "days_on_market": dom,
        "score_breakdown": breakdown,
        "scored_at": datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# Batch job — called by ARQ worker
# ---------------------------------------------------------------------------


async def run_scoring_batch(ctx: dict | None = None) -> dict[str, Any]:
    """Score all listings that need scoring.

    Queries every non-stale listing that either has no score yet, or whose
    score pre-dates the most recent enrichment run.  Results are upserted into
    ``listing_score``.  Called by the ARQ scheduler after each enrichment pass.

    Args:
        ctx: ARQ worker context (not used directly; kept for ARQ compatibility).

    Returns:
        Dictionary with ``status`` (``"success"`` or ``"error"``) and
        ``scored`` count on success.
    """
    from sqlalchemy import and_, func, or_
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from libs.common.db import SessionLocal
    from libs.common.models import (
        ListingDetailORM,
        ListingEnrichment,
        ListingObservation,
        ListingScore,
        MarketPriceNormal,
        ProductDailyMetrics,
        ProductTemplate,
    )

    db = SessionLocal()
    try:
        candidates = (
            db.query(
                ListingObservation,
                ListingDetailORM,
                ListingEnrichment,
                MarketPriceNormal,
                ProductDailyMetrics,
                ProductTemplate,
            )
            .outerjoin(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
            .outerjoin(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
            .outerjoin(
                MarketPriceNormal,
                MarketPriceNormal.product_id == ListingObservation.product_id,
            )
            .outerjoin(
                ProductDailyMetrics,
                and_(
                    ProductDailyMetrics.product_id == ListingObservation.product_id,
                    ProductDailyMetrics.date == func.current_date(),
                ),
            )
            .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
            .outerjoin(ListingScore, ListingScore.obs_id == ListingObservation.obs_id)
            .filter(
                ListingObservation.is_stale == False,  # noqa: E712
                or_(
                    ListingScore.obs_id.is_(None),
                    ListingScore.scored_at < ListingEnrichment.enriched_at,
                ),
            )
            .all()
        )

        logger.info("Scoring batch: %d candidates", len(candidates))
        scored = 0

        for obs, detail, enrichment, pmn_row, metrics, product in candidates:
            scores = compute_all_scores(obs, detail, enrichment, pmn_row, metrics, product)
            stmt = pg_insert(ListingScore).values(**scores)
            stmt = stmt.on_conflict_do_update(
                index_elements=["obs_id"],
                set_={k: v for k, v in scores.items() if k != "obs_id"},
            )
            db.execute(stmt)
            scored += 1

        db.commit()
        logger.info("Scoring batch: %d scored", scored)
        return {"status": "success", "scored": scored}

    except Exception:
        db.rollback()
        logger.exception("Scoring batch failed")
        return {"status": "error"}

    finally:
        db.close()

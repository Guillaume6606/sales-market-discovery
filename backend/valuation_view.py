"""Read-time valuation replaces cached monetary scores in operator responses."""

from typing import Any

from libs.common.models import ListingScore
from libs.common.utils import decimal_to_float


def current_score(valuation: dict[str, Any], cached: ListingScore | None) -> dict[str, Any]:
    eligible = valuation.get("eligible", False)
    contribution = valuation.get("contribution_eur") if eligible else None
    acquisition = valuation.get("acquisition_cost_eur") if eligible else None
    costs = valuation.get("cost_breakdown") or {}
    return {
        "arbitrage_spread_eur": decimal_to_float(contribution),
        "net_roi_pct": float(contribution / acquisition * 100)
        if contribution is not None and acquisition
        else None,
        "risk_adjusted_confidence": decimal_to_float(cached.risk_adjusted_confidence)
        if eligible and cached
        else 0,
        "acquisition_cost_eur": decimal_to_float(acquisition),
        "estimated_sale_price_eur": decimal_to_float(valuation.get("estimated_sale_price_eur"))
        if eligible
        else None,
        "estimated_sell_fees_eur": decimal_to_float(costs.get("sell_fee_eur"))
        if eligible
        else None,
        "estimated_sell_shipping_eur": decimal_to_float(costs.get("outbound_logistics_eur"))
        if eligible
        else None,
        "days_on_market": cached.days_on_market if cached else None,
        "score_breakdown": {
            "verified_valuation": valuation,
            "confidence_label": "uncalibrated_heuristic",
        },
        "scored_at": cached.scored_at.isoformat() if cached and cached.scored_at else None,
    }

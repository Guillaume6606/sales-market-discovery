from decimal import Decimal
from types import SimpleNamespace


def test_expired_reference_cannot_display_cached_profit():
    from backend.valuation_view import current_score

    cached = SimpleNamespace(
        arbitrage_spread_eur=999, risk_adjusted_confidence=99, days_on_market=2, scored_at=None
    )
    result = current_score({"eligible": False, "reasons": ["reference_expired"]}, cached)
    assert result["arbitrage_spread_eur"] is None
    assert result["risk_adjusted_confidence"] == 0


def test_display_profit_is_recomputed_from_current_reference():
    from backend.valuation_view import current_score

    result = current_score(
        {
            "eligible": True,
            "reasons": [],
            "contribution_eur": Decimal("25"),
            "acquisition_cost_eur": Decimal("100"),
            "estimated_sale_price_eur": Decimal("150"),
            "cost_breakdown": {
                "sell_fee_eur": Decimal("10"),
                "outbound_logistics_eur": Decimal("15"),
            },
        },
        None,
    )
    assert result["arbitrage_spread_eur"] == 25
    assert result["net_roi_pct"] == 25

"""Smoke tests: composite scoring business logic on real numbers."""

from __future__ import annotations

from decimal import Decimal

import pytest

composite_scoring = pytest.importorskip(
    "ingestion.composite_scoring",
    reason="ingestion package not installed (running in Docker with mounted tests only)",
)
compute_acquisition_cost = composite_scoring.compute_acquisition_cost
compute_arbitrage_spread = composite_scoring.compute_arbitrage_spread
compute_estimated_sale_price = composite_scoring.compute_estimated_sale_price
compute_net_roi = composite_scoring.compute_net_roi
compute_risk_adjusted_confidence = composite_scoring.compute_risk_adjusted_confidence
compute_sell_fees = composite_scoring.compute_sell_fees
get_sell_shipping_estimate = composite_scoring.get_sell_shipping_estimate


class TestScoringBusinessLogic:
    def test_iphone_good_deal(self) -> None:
        """A listing priced well below PMN should yield a positive spread and ROI > 20%."""
        acq = compute_acquisition_cost(Decimal("500"), Decimal("10"), "vinted", False)
        sale = compute_estimated_sale_price(Decimal("800"), "like_new", True, False, False)
        fees = compute_sell_fees(sale, "ebay")
        shipping = get_sell_shipping_estimate("electronics")
        spread = compute_arbitrage_spread(sale, fees, shipping, acq)
        roi = compute_net_roi(spread, acq)
        assert spread > Decimal("0")
        assert roi > Decimal("20")

    def test_overpriced_listing(self) -> None:
        """A listing priced above PMN with condition discount should yield a negative spread."""
        acq = compute_acquisition_cost(Decimal("900"), Decimal("10"), "ebay", False)
        sale = compute_estimated_sale_price(Decimal("800"), "good", False, False, False)
        fees = compute_sell_fees(sale, "ebay")
        shipping = get_sell_shipping_estimate("electronics")
        spread = compute_arbitrage_spread(sale, fees, shipping, acq)
        assert spread < Decimal("0")

    def test_high_confidence_good_signals(self) -> None:
        """Strong positive signals should produce a confidence score above 80."""
        conf = compute_risk_adjusted_confidence(0.9, 0.05, 0.9, 0.85, 0.1, 0.8)
        assert conf > 80.0

    def test_suspicious_low_confidence(self) -> None:
        """Weak / suspicious signals should produce a confidence score below 50."""
        conf = compute_risk_adjusted_confidence(0.2, 0.8, 0.3, 0.7, 0.5, 0.3)
        assert conf < 50.0

    def test_local_pickup_advantage(self) -> None:
        """Local-pickup listings should have lower acquisition cost (no shipping added)."""
        shipped = compute_acquisition_cost(Decimal("100"), Decimal("15"), "leboncoin", False)
        pickup = compute_acquisition_cost(Decimal("100"), Decimal("15"), "leboncoin", True)
        assert pickup < shipped
        assert pickup == Decimal("100")

"""Tests for composite scoring functions."""

from decimal import Decimal

from ingestion.composite_scoring import (
    CONFIDENCE_WEIGHTS,
    compute_acquisition_cost,
    compute_arbitrage_spread,
    compute_estimated_sale_price,
    compute_net_roi,
    compute_risk_adjusted_confidence,
    compute_sell_fees,
)


class TestAcquisitionCost:
    def test_basic_cost(self):
        cost = compute_acquisition_cost(Decimal("100"), Decimal("10"), "ebay", False)
        assert cost == Decimal("110")

    def test_vinted_buyer_fee(self):
        cost = compute_acquisition_cost(Decimal("100"), Decimal("5"), "vinted", False)
        assert cost == Decimal("110")  # 100 + 5 + 5% of 100

    def test_local_pickup_zero_shipping(self):
        cost = compute_acquisition_cost(Decimal("100"), Decimal("15"), "leboncoin", True)
        assert cost == Decimal("100")

    def test_null_shipping(self):
        cost = compute_acquisition_cost(Decimal("100"), None, "ebay", False)
        assert cost == Decimal("100")

    def test_cost_never_less_than_price(self):
        cost = compute_acquisition_cost(Decimal("50"), Decimal("0"), "ebay", False)
        assert cost >= Decimal("50")


class TestEstimatedSalePrice:
    def test_like_new_no_extras(self):
        price = compute_estimated_sale_price(Decimal("100"), "like_new", False, False, False)
        assert price == Decimal("100.00")

    def test_new_with_all_extras(self):
        price = compute_estimated_sale_price(Decimal("100"), "new", True, True, True)
        expected = (
            Decimal("100") * Decimal("1.10") * Decimal("1.05") * Decimal("1.05") * Decimal("1.05")
        )
        assert price is not None
        assert abs(price - expected) < Decimal("0.01")

    def test_fair_condition(self):
        price = compute_estimated_sale_price(Decimal("100"), "fair", False, False, False)
        assert price == Decimal("75.00")

    def test_unknown_defaults_good(self):
        price = compute_estimated_sale_price(Decimal("100"), None, False, False, False)
        assert price == Decimal("90.00")

    def test_no_pmn_returns_none(self):
        assert compute_estimated_sale_price(None, "good", False, False, False) is None


class TestSellFees:
    def test_ebay_fees(self):
        fees = compute_sell_fees(Decimal("100"), "ebay")
        assert abs(fees - Decimal("15.90")) < Decimal("0.01")

    def test_vinted_fees(self):
        fees = compute_sell_fees(Decimal("100"), "vinted")
        assert abs(fees - Decimal("8.00")) < Decimal("0.01")


class TestArbitrageSpread:
    def test_positive_spread(self):
        assert compute_arbitrage_spread(
            Decimal("200"), Decimal("32"), Decimal("8"), Decimal("120")
        ) == Decimal("40")

    def test_negative_spread(self):
        assert compute_arbitrage_spread(
            Decimal("100"), Decimal("16"), Decimal("8"), Decimal("120")
        ) == Decimal("-44")


class TestNetROI:
    def test_positive_roi(self):
        roi = compute_net_roi(Decimal("50"), Decimal("100"))
        assert roi == Decimal("50.00")

    def test_zero_acquisition(self):
        assert compute_net_roi(Decimal("50"), Decimal("0")) is None


class TestRiskAdjustedConfidence:
    def test_all_perfect(self):
        assert compute_risk_adjusted_confidence(1.0, 0.0, 1.0, 1.0, 0.0, 1.0) == 100.0

    def test_all_worst(self):
        assert compute_risk_adjusted_confidence(0.0, 1.0, 0.0, 0.0, 1.0, 0.0) == 0.0

    def test_neutral_around_50(self):
        score = compute_risk_adjusted_confidence(0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        assert abs(score - 50.0) < 1.0

    def test_no_pmn_capped_at_40(self):
        score = compute_risk_adjusted_confidence(1.0, 0.0, 1.0, None, 0.0, 1.0)
        assert score <= 40.0

    def test_weights_sum_to_one(self):
        assert abs(sum(CONFIDENCE_WEIGHTS.values()) - 1.0) < 0.001

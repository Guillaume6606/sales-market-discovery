"""Tests for computation engine (estimate_margin, compute_liquidity_score, compute_pmn_for_product)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from ingestion.computation import (
    compute_liquidity_score,
    compute_pmn_for_product,
    estimate_margin,
)

# ============================================================================
# TestEstimateMargin — pure function, no DB
# ============================================================================


class TestEstimateMargin:
    def test_ebay_fees(self):
        """eBay: 12.9% commission + 3% payment."""
        result = estimate_margin(80.0, 100.0, 0.0, "ebay")
        assert result["risk_level"] != "unknown"
        # Fees on resale (100): 12.9 + 3.0 = 15.9
        assert result["fees"]["platform_fee"] == pytest.approx(12.9, abs=0.01)
        assert result["fees"]["payment_fee"] == pytest.approx(3.0, abs=0.01)
        # Gross margin: 100 - 80 = 20
        assert result["gross_margin"] == pytest.approx(20.0, abs=0.01)
        # Net margin: 20 - 15.9 = 4.1
        assert result["net_margin"] == pytest.approx(4.1, abs=0.01)

    def test_leboncoin_fees(self):
        """LeBonCoin: 5% commission + 3% payment."""
        result = estimate_margin(80.0, 100.0, 0.0, "leboncoin")
        assert result["fees"]["platform_fee"] == pytest.approx(5.0, abs=0.01)
        assert result["fees"]["payment_fee"] == pytest.approx(3.0, abs=0.01)

    def test_vinted_fees(self):
        """Vinted: 5% commission + 3% payment."""
        result = estimate_margin(80.0, 100.0, 0.0, "vinted")
        assert result["fees"]["platform_fee"] == pytest.approx(5.0, abs=0.01)
        assert result["fees"]["payment_fee"] == pytest.approx(3.0, abs=0.01)

    def test_unknown_platform_defaults(self):
        """Unknown platform: 10% commission + 3% payment."""
        result = estimate_margin(80.0, 100.0, 0.0, "unknown_platform")
        assert result["fees"]["platform_fee"] == pytest.approx(10.0, abs=0.01)
        assert result["fees"]["payment_fee"] == pytest.approx(3.0, abs=0.01)

    def test_negative_margin_risk(self):
        """Price > PMN → very_high risk."""
        result = estimate_margin(120.0, 100.0, 0.0, "ebay")
        assert result["risk_level"] == "very_high"

    def test_none_inputs(self):
        """None price or PMN → unknown risk."""
        result = estimate_margin(None, 100.0, 0.0, "ebay")
        assert result["risk_level"] == "unknown"
        assert result["gross_margin"] is None

        result2 = estimate_margin(80.0, None, 0.0, "ebay")
        assert result2["risk_level"] == "unknown"

    def test_zero_price(self):
        """Zero price → unknown risk (division guard)."""
        result = estimate_margin(0.0, 100.0, 0.0, "ebay")
        assert result["risk_level"] == "unknown"
        assert result["net_margin"] is None


# ============================================================================
# TestComputeLiquidityScore — mock DB session
# ============================================================================


class TestComputeLiquidityScore:
    def _mock_db(self, sold_30d: int = 0, sold_7d: int = 0, active: int = 0):
        db = MagicMock()
        # Each .query().filter().scalar() call returns different values
        scalar_results = [sold_30d, sold_7d, active]
        scalar_mock = MagicMock()
        scalar_mock.scalar = MagicMock(side_effect=scalar_results)
        db.query.return_value.filter.return_value = scalar_mock
        return db

    def test_high_velocity(self):
        """30 sales/30d → velocity_score ≈ 50."""
        db = self._mock_db(sold_30d=30, sold_7d=10, active=5)
        result = compute_liquidity_score("test-product", db=db)
        assert result["liquidity_score"] > 0
        assert result["sold_count_30d"] == 30

    def test_no_sales(self):
        """0 sales → all scores 0."""
        db = self._mock_db(sold_30d=0, sold_7d=0, active=0)
        result = compute_liquidity_score("test-product", db=db)
        assert result["liquidity_score"] == 0.0

    def test_market_depth(self):
        """20 active listings → depth_score ≈ 25."""
        db = self._mock_db(sold_30d=0, sold_7d=0, active=20)
        result = compute_liquidity_score("test-product", db=db)
        # With 0 sales but 20 active, depth contributes 25
        assert result["liquidity_score"] == pytest.approx(25.0, abs=0.5)


# ============================================================================
# TestComputePmnForProduct — mock DB session
# ============================================================================


class TestComputePmnForProduct:
    def _make_mock_obs(self, price: float, days_ago: int = 5):
        obs = MagicMock()
        obs.price = price
        obs.observed_at = datetime.now(UTC) - timedelta(days=days_ago)
        return obs

    def test_sufficient_sold_items(self):
        """20+ sold items → success."""
        db = MagicMock()
        # Product exists
        product = MagicMock()
        product.product_id = "test-pid"

        sold_items = [self._make_mock_obs(100.0 + i) for i in range(25)]

        # query(ProductTemplate).filter().first() → product
        # query(price, observed_at).filter().all() → sold_items
        # query(MarketPriceNormal).filter().first() → None (new)
        call_count = [0]

        def side_effect_query(*args):
            mock_chain = MagicMock()
            call_count[0] += 1
            call_idx = call_count[0]

            if call_idx == 1:
                # ProductTemplate lookup
                mock_chain.filter.return_value.first.return_value = product
            elif call_idx == 2:
                # Sold items query
                mock_chain.filter.return_value.all.return_value = sold_items
            elif call_idx == 3:
                # MarketPriceNormal lookup
                mock_chain.filter.return_value.first.return_value = None
            return mock_chain

        db.query.side_effect = side_effect_query
        db.add = MagicMock()
        db.commit = MagicMock()

        result = compute_pmn_for_product("test-pid", db=db)
        assert result["status"] == "success"
        assert result["pmn"] is not None
        assert result["sample_size"] > 0

    def test_fallback_to_active(self):
        """<10 sold items → includes active listings."""
        db = MagicMock()
        product = MagicMock()
        product.product_id = "test-pid"

        sold_items = [self._make_mock_obs(100.0 + i) for i in range(5)]
        active_items = [self._make_mock_obs(95.0 + i) for i in range(15)]

        call_count = [0]

        def side_effect_query(*args):
            mock_chain = MagicMock()
            call_count[0] += 1
            call_idx = call_count[0]

            if call_idx == 1:
                mock_chain.filter.return_value.first.return_value = product
            elif call_idx == 2:
                mock_chain.filter.return_value.all.return_value = sold_items
            elif call_idx == 3:
                mock_chain.filter.return_value.all.return_value = active_items
            elif call_idx == 4:
                mock_chain.filter.return_value.first.return_value = None
            return mock_chain

        db.query.side_effect = side_effect_query
        db.add = MagicMock()
        db.commit = MagicMock()

        result = compute_pmn_for_product("test-pid", db=db)
        assert result["status"] == "success"

    def test_insufficient_data(self):
        """<3 prices → insufficient_data."""
        db = MagicMock()
        product = MagicMock()
        product.product_id = "test-pid"

        sold_items = [self._make_mock_obs(100.0)]
        active_items = [self._make_mock_obs(95.0)]

        call_count = [0]

        def side_effect_query(*args):
            mock_chain = MagicMock()
            call_count[0] += 1
            call_idx = call_count[0]

            if call_idx == 1:
                mock_chain.filter.return_value.first.return_value = product
            elif call_idx == 2:
                mock_chain.filter.return_value.all.return_value = sold_items
            elif call_idx == 3:
                mock_chain.filter.return_value.all.return_value = active_items
            return mock_chain

        db.query.side_effect = side_effect_query

        result = compute_pmn_for_product("test-pid", db=db)
        assert result["status"] == "insufficient_data"
        assert result["price_count"] == 2

    def test_product_not_found(self):
        """Non-existent product → error."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = compute_pmn_for_product("nonexistent", db=db)
        assert result["status"] == "error"
        assert result["error"] == "product_not_found"

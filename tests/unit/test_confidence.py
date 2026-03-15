"""Tests for PMN confidence computation."""

import pytest

from ingestion.computation import compute_pmn_confidence


class TestComputePmnConfidence:
    def test_full_confidence(self):
        """30 samples, 0 days old, 0 std → 1.0."""
        result = compute_pmn_confidence(
            sample_size=30, newest_sale_age_days=0.0, std_dev=0.0, pmn=100.0
        )
        assert result == pytest.approx(1.0)

    def test_zero_samples(self):
        """0 samples → sample_factor = 0, so score is just freshness + consistency."""
        result = compute_pmn_confidence(
            sample_size=0, newest_sale_age_days=0.0, std_dev=0.0, pmn=100.0
        )
        # 0.4 * 0.0 + 0.3 * 1.0 + 0.3 * 1.0 = 0.6
        assert result == pytest.approx(0.6)

    def test_stale_data(self):
        """30 samples, 30 days old → freshness_factor = 0."""
        result = compute_pmn_confidence(
            sample_size=30, newest_sale_age_days=30.0, std_dev=0.0, pmn=100.0
        )
        # 0.4 * 1.0 + 0.3 * 0.0 + 0.3 * 1.0 = 0.7
        assert result == pytest.approx(0.7)

    def test_high_variance(self):
        """std_dev == pmn → consistency_factor = 0."""
        result = compute_pmn_confidence(
            sample_size=30, newest_sale_age_days=0.0, std_dev=100.0, pmn=100.0
        )
        # 0.4 * 1.0 + 0.3 * 1.0 + 0.3 * 0.0 = 0.7
        assert result == pytest.approx(0.7)

    def test_pmn_zero(self):
        """pmn = 0 → consistency_factor = 0."""
        result = compute_pmn_confidence(
            sample_size=30, newest_sale_age_days=0.0, std_dev=10.0, pmn=0.0
        )
        # 0.4 * 1.0 + 0.3 * 1.0 + 0.3 * 0.0 = 0.7
        assert result == pytest.approx(0.7)

    def test_small_sample(self):
        """15 samples → sample_factor = 0.5."""
        result = compute_pmn_confidence(
            sample_size=15, newest_sale_age_days=0.0, std_dev=0.0, pmn=100.0
        )
        # 0.4 * 0.5 + 0.3 * 1.0 + 0.3 * 1.0 = 0.8
        assert result == pytest.approx(0.8)

    def test_clamps_negative_freshness(self):
        """Data older than 30 days → freshness_factor clamped to 0."""
        result = compute_pmn_confidence(
            sample_size=30, newest_sale_age_days=60.0, std_dev=0.0, pmn=100.0
        )
        # freshness_factor = max(0, 1 - 60/30) = 0
        # 0.4 * 1.0 + 0.3 * 0.0 + 0.3 * 1.0 = 0.7
        assert result == pytest.approx(0.7)

    def test_sample_capped_at_one(self):
        """More than 30 samples → sample_factor capped at 1.0."""
        result = compute_pmn_confidence(
            sample_size=100, newest_sale_age_days=0.0, std_dev=0.0, pmn=100.0
        )
        assert result == pytest.approx(1.0)

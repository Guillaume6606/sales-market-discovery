from datetime import UTC, datetime, timedelta

import pandas as pd

from ingestion.pricing import iqr_clip, pmn_from_prices


class TestPmnFromPrices:
    def test_empty_list_returns_none(self) -> None:
        result = pmn_from_prices([])
        assert result["pmn"] is None
        assert result["methodology"]["reason"] == "no_data"
        assert result["n"] == 0

    def test_single_price(self) -> None:
        result = pmn_from_prices([100.0])
        assert result["pmn"] == 100.0
        assert result["n"] == 1

    def test_two_prices_simple_median(self) -> None:
        result = pmn_from_prices([100.0, 200.0])
        assert result["pmn"] == 150.0
        assert result["methodology"]["outlier_filter"] == "none"

    def test_twenty_plus_prices_near_median(self) -> None:
        prices = [100.0 + i for i in range(25)]
        result = pmn_from_prices(prices)
        assert result["pmn"] is not None
        # Median of 100-124 is 112; after 5-95% clipping it should be close
        assert 105 < result["pmn"] < 120
        assert result["methodology"]["outlier_filter"] == "percentile_5_95"

    def test_extreme_outliers_filtered(self) -> None:
        prices = [100.0] * 20 + [10000.0, 1.0]
        result = pmn_from_prices(prices)
        assert result["pmn"] is not None
        # With outliers filtered, PMN should be near 100
        assert 95 < result["pmn"] < 110

    def test_identical_prices_zero_std(self) -> None:
        prices = [50.0] * 10
        result = pmn_from_prices(prices)
        assert result["pmn"] == 50.0
        assert result["pmn_low"] == result["pmn_high"]

    def test_time_weighted_with_timestamps(self) -> None:
        now = datetime.now(UTC)
        prices = [100.0, 200.0, 300.0, 150.0, 250.0]
        timestamps = [now - timedelta(days=i * 10) for i in range(5)]
        result = pmn_from_prices(prices, timestamps=timestamps, time_weighted=True)
        assert result["pmn"] is not None
        assert result["methodology"]["method"] == "weighted_median"

    def test_methodology_has_expected_keys(self) -> None:
        result = pmn_from_prices([100.0, 200.0, 300.0, 400.0])
        methodology = result["methodology"]
        assert "method" in methodology
        assert "outlier_filter" in methodology
        assert "sample_size" in methodology


class TestIqrClip:
    def test_outlier_removal(self) -> None:
        s = pd.Series([1, 2, 3, 4, 5, 100])
        clipped = iqr_clip(s)
        assert 100 not in clipped.values

    def test_empty_series(self) -> None:
        s = pd.Series([], dtype=float)
        clipped = iqr_clip(s)
        assert len(clipped) == 0

    def test_single_value(self) -> None:
        s = pd.Series([42.0])
        clipped = iqr_clip(s)
        assert len(clipped) == 1
        assert clipped.iloc[0] == 42.0

    def test_custom_k(self) -> None:
        s = pd.Series([1, 2, 3, 4, 5, 10])
        # Tighter k should remove more
        clipped_tight = iqr_clip(s, k=0.5)
        clipped_loose = iqr_clip(s, k=3.0)
        assert len(clipped_tight) <= len(clipped_loose)

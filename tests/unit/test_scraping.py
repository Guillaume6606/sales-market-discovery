"""Unit tests for scraping stealth utilities."""

import statistics

from libs.common.scraping import human_delay


class TestHumanDelay:
    def test_returns_float(self) -> None:
        assert isinstance(human_delay(1.0, 3.0), float)

    def test_result_clamped_to_bounds(self) -> None:
        samples = [human_delay(1.0, 3.0) for _ in range(1000)]
        assert all(1.0 <= s <= 3.0 for s in samples)

    def test_higher_min_shifts_distribution_up(self) -> None:
        low = [human_delay(0.5, 1.5) for _ in range(500)]
        high = [human_delay(3.0, 6.0) for _ in range(500)]
        assert statistics.mean(high) > statistics.mean(low)

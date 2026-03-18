"""Unit tests for scraping stealth utilities."""

import statistics

from libs.common.scraping import DATADOME_PATTERNS, DataDomeBlockError, human_delay


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


class TestDataDomePatterns:
    def test_matches_datadome_keyword(self) -> None:
        assert DATADOME_PATTERNS.search("<title>DataDome</title>") is not None

    def test_matches_captcha_delivery(self) -> None:
        assert DATADOME_PATTERNS.search("geo.captcha-delivery.com") is not None

    def test_matches_dd_script(self) -> None:
        assert DATADOME_PATTERNS.search('<script src="dd.js"></script>') is not None

    def test_no_match_on_normal_page(self) -> None:
        assert DATADOME_PATTERNS.search("<h1>iPhone 14 Pro — 85 €</h1>") is None

    def test_case_insensitive(self) -> None:
        assert DATADOME_PATTERNS.search("DATADOME") is not None


class TestDataDomeBlockError:
    def test_is_runtime_error(self) -> None:
        assert issubclass(DataDomeBlockError, RuntimeError)

    def test_stores_url_in_message(self) -> None:
        url = "https://www.vinted.fr/items/456"
        err = DataDomeBlockError(url)
        assert url in str(err)
        assert err.url == url

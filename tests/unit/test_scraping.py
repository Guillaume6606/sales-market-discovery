"""Unit tests for scraping stealth utilities."""

import statistics
from pathlib import Path

from libs.common.scraping import (
    DATADOME_PATTERNS,
    VINTED_COOKIE_PATH,
    DataDomeBlockError,
    human_delay,
)


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

    def test_rejects_zero_min(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="min_s must be > 0"):
            human_delay(0, 3.0)

    def test_rejects_negative_max(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="max_s must be > 0"):
            human_delay(1.0, -1.0)


class TestDataDomePatterns:
    def test_matches_datadome_keyword(self) -> None:
        assert DATADOME_PATTERNS.search("<title>DataDome</title>") is not None

    def test_matches_captcha_delivery(self) -> None:
        assert DATADOME_PATTERNS.search("geo.captcha-delivery.com") is not None

    def test_matches_dd_script_path(self) -> None:
        assert DATADOME_PATTERNS.search('<script src="/dd.js"></script>') is not None

    def test_no_match_on_similar_filename(self) -> None:
        # /dd.js requires a path separator — should not match "odd.js" or "todd.js"
        assert DATADOME_PATTERNS.search('<script src="todd.js"></script>') is None

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


class TestVintedCookiePath:
    def test_is_path_instance(self) -> None:
        assert isinstance(VINTED_COOKIE_PATH, Path)

    def test_under_tmp_pwuser(self) -> None:
        assert str(VINTED_COOKIE_PATH).startswith("/tmp/pwuser")

    def test_is_json(self) -> None:
        assert VINTED_COOKIE_PATH.suffix == ".json"

    def test_name_contains_vinted(self) -> None:
        assert "vinted" in VINTED_COOKIE_PATH.name


class TestCapturePageSignature:
    def test_method_exists_and_is_async(self) -> None:
        import inspect

        from libs.common.scraping import ScrapingSession

        session = ScrapingSession()
        method = getattr(session, "capture_page", None)
        assert method is not None
        assert inspect.iscoroutinefunction(method)

    def test_accepts_url_and_referer(self) -> None:
        import inspect

        from libs.common.scraping import ScrapingSession

        sig = inspect.signature(ScrapingSession().capture_page)
        assert "url" in sig.parameters
        assert "referer" in sig.parameters

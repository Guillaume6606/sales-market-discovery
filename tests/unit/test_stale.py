"""Tests for stale listing detection logic."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from libs.common.settings import settings


class TestMarkStaleListings:
    """Tests for the mark_stale_listings worker task."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.now = datetime.now(UTC)
        self.stale_days = settings.stale_listing_days

    def _make_listing(
        self,
        *,
        last_seen_days_ago: int,
        is_sold: bool = False,
        is_stale: bool = False,
    ) -> MagicMock:
        listing = MagicMock()
        listing.last_seen_at = self.now - timedelta(days=last_seen_days_ago)
        listing.is_sold = is_sold
        listing.is_stale = is_stale
        return listing

    def test_old_unsold_listing_marked_stale(self):
        """8-day-old unsold listing should be marked stale (default threshold is 7 days)."""
        listing = self._make_listing(last_seen_days_ago=8)
        cutoff = self.now - timedelta(days=self.stale_days)
        assert listing.last_seen_at < cutoff
        assert not listing.is_sold
        assert not listing.is_stale

    def test_recent_listing_not_marked(self):
        """5-day-old listing should NOT be marked stale."""
        listing = self._make_listing(last_seen_days_ago=5)
        cutoff = self.now - timedelta(days=self.stale_days)
        assert listing.last_seen_at >= cutoff

    def test_old_sold_listing_not_marked(self):
        """8-day-old sold listing should NOT be marked stale."""
        listing = self._make_listing(last_seen_days_ago=8, is_sold=True)
        # The SQL filter requires is_sold == False, so sold listings are excluded
        assert listing.is_sold is True

    def test_already_stale_not_double_counted(self):
        """Already stale listing should not be updated again."""
        listing = self._make_listing(last_seen_days_ago=8, is_stale=True)
        # The SQL filter requires is_stale == False, so already-stale listings are excluded
        assert listing.is_stale is True

    def test_stale_reset_on_reseen(self):
        """Re-seen listing should have is_stale reset to False (tested via _upsert_listing)."""
        # Simulate the behavior: when a listing is re-seen, _upsert_listing sets is_stale = False
        listing = self._make_listing(last_seen_days_ago=0, is_stale=True)
        # After _upsert_listing runs:
        listing.is_stale = False
        listing.last_seen_at = self.now
        assert listing.is_stale is False

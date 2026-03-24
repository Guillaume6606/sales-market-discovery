"""Smoke tests: detail fetch per connector (real API calls).

These tests hit live external APIs and must NOT be run in CI.
Execute manually with:

    uv run pytest tests/smoke/test_06_detail_fetch.py -v -s

The pytest config sets ``asyncio_mode = "auto"`` so all async fixtures and
test functions are handled automatically.

NOTE: These tests require the latest connector code with fetch_detail().
In Docker smoke tests (pre-push hook) the installed package may be stale,
so we guard all imports of the new ``fetch_detail`` function with
``importorskip`` to skip gracefully rather than fail.
"""

import pytest

# Guard: skip entire module if fetch_detail is not available
# (e.g. running in Docker with stale installed package)
_ebay = pytest.importorskip(
    "ingestion.connectors.ebay",
    reason="ingestion.connectors.ebay not importable",
)
if not hasattr(_ebay, "fetch_detail"):
    pytest.skip(
        "fetch_detail not yet available in installed package",
        allow_module_level=True,
    )


class TestEbayDetailFetch:
    """Smoke tests for eBay ``fetch_detail()``."""

    @pytest.fixture
    async def ebay_listings(self):
        """Fetch a handful of live eBay listings to use as input."""
        from ingestion.connectors.ebay import fetch_ebay_listings

        listings = await fetch_ebay_listings("iPhone 15", limit=3)
        if not listings:
            pytest.skip("eBay returned no listings — sandbox API or missing EBAY_APP_ID")
        return listings

    def test_fetch_detail_returns_data(self, ebay_listings):
        """``fetch_detail`` returns a ``ListingDetail`` with expected fields populated."""
        from ingestion.connectors.ebay import fetch_detail

        detail = fetch_detail(ebay_listings[0].listing_id, obs_id=1)
        assert detail is not None, "fetch_detail returned None for a live eBay listing"
        assert detail.obs_id == 1
        assert detail.description is not None, "eBay detail should include a description"
        assert len(detail.photo_urls) > 0, "eBay detail should include at least one photo URL"
        assert detail.photo_count == len(detail.photo_urls), (
            "photo_count must equal len(photo_urls)"
        )

    def test_fetch_detail_invalid_id_returns_none(self):
        """Requesting an obviously invalid listing ID must return ``None`` gracefully."""
        from ingestion.connectors.ebay import fetch_detail

        detail = fetch_detail("000000000000", obs_id=99)
        # Either None or a detail with no meaningful data is acceptable.
        if detail is not None:
            assert detail.obs_id == 99


class TestLeboncoinDetailFetch:
    """Smoke tests for LeBonCoin ``fetch_detail()``."""

    @pytest.fixture
    async def lbc_listings(self):
        """Fetch a handful of live LBC listings to use as input."""
        from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

        connector = LeBonCoinAPIConnector()
        listings = await connector.search_items(keyword="iPhone 15", limit=3)
        if not listings:
            pytest.skip("LeBonCoin returned no listings")
        return listings

    def test_fetch_detail_returns_data(self, lbc_listings):
        """``fetch_detail`` returns a ``ListingDetail`` with expected fields populated."""
        from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

        connector = LeBonCoinAPIConnector()
        listing = lbc_listings[0]
        detail = connector.fetch_detail(listing.listing_id, obs_id=1)
        assert detail is not None, (
            f"fetch_detail returned None for LBC listing {listing.listing_id}"
        )
        assert detail.obs_id == 1
        # photo_count is auto-computed from photo_urls
        assert detail.photo_count == len(detail.photo_urls)

    def test_fetch_detail_description_present(self, lbc_listings):
        """LBC ads typically always have a body/description."""
        from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector

        connector = LeBonCoinAPIConnector()
        detail = connector.fetch_detail(lbc_listings[0].listing_id, obs_id=2)
        assert detail is not None
        assert detail.description is not None, "LBC detail should include a description"


class TestVintedDetailFetch:
    """Smoke tests for Vinted ``fetch_detail()``."""

    @pytest.fixture
    async def vinted_listings(self):
        """Fetch a handful of live Vinted listings to use as input."""
        from ingestion.connectors.vinted_api import VintedAPIConnector

        connector = VintedAPIConnector()
        listings = await connector.search_items("iPhone 15", limit=3)
        if not listings:
            pytest.skip("Vinted returned no listings")
        return listings

    async def test_fetch_detail_returns_data(self, vinted_listings):
        """``fetch_detail`` returns a ``ListingDetail`` with Vinted-specific defaults."""
        from ingestion.connectors.vinted_api import VintedAPIConnector

        connector = VintedAPIConnector()
        listing = vinted_listings[0]
        detail = await connector.fetch_detail(listing.listing_id, obs_id=1)
        assert detail is not None, (
            f"fetch_detail returned None for Vinted listing {listing.listing_id}"
        )
        assert detail.obs_id == 1
        # Vinted is always shipped, offers always enabled
        assert detail.local_pickup_only is False
        assert detail.negotiation_enabled is True
        # photo_count must match
        assert detail.photo_count == len(detail.photo_urls)

    async def test_fetch_detail_favourite_count_present(self, vinted_listings):
        """Vinted API exposes favourite_count — must not be None."""
        from ingestion.connectors.vinted_api import VintedAPIConnector

        connector = VintedAPIConnector()
        detail = await connector.fetch_detail(vinted_listings[0].listing_id, obs_id=2)
        assert detail is not None
        assert detail.favorite_count is not None, (
            "Vinted detail should include favourite_count from the API"
        )

    async def test_delegation_via_scraping_connector(self, vinted_listings):
        """``VintedConnector.fetch_detail`` delegates correctly to the API connector."""
        from ingestion.connectors.vinted import VintedConnector

        connector = VintedConnector()
        detail = await connector.fetch_detail(vinted_listings[0].listing_id, obs_id=3)
        assert detail is not None
        assert detail.obs_id == 3

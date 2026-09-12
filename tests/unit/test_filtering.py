from unittest.mock import patch

import pytest

from ingestion.filtering import (
    _matches_brand,
    _matches_price,
    _matches_words_to_avoid,
    filter_listings_multi_stage,
)
from ingestion.schemas import ProductTemplateSnapshot


class TestMatchesPrice:
    def test_within_range(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(price=600.0)
        assert _matches_price(sample_snapshot, listing) is True

    def test_below_min(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(price=100.0)
        assert _matches_price(sample_snapshot, listing) is False

    def test_above_max(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(price=2000.0)
        assert _matches_price(sample_snapshot, listing) is False

    def test_none_price_with_range(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(price=None)
        assert _matches_price(sample_snapshot, listing) is False

    def test_no_bounds(self, sample_snapshot, listing_factory) -> None:
        snapshot = ProductTemplateSnapshot(
            **{
                **sample_snapshot.__dict__,
                "price_min": None,
                "price_max": None,
            }
        )
        listing = listing_factory(price=9999.0)
        assert _matches_price(snapshot, listing) is True


class TestMatchesBrand:
    def test_exact_match(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(brand="Apple")
        assert _matches_brand(sample_snapshot, listing) is True

    def test_brand_in_title(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(title="Apple iPhone 14 Pro", brand=None)
        assert _matches_brand(sample_snapshot, listing) is True

    def test_brand_in_search_query(self, listing_factory) -> None:
        snapshot = ProductTemplateSnapshot(
            product_id="00000000-0000-0000-0000-000000000001",
            name="Test",
            description=None,
            search_query="Apple iPhone 14",
            category_id="00000000-0000-0000-0000-000000000010",
            category_name="Smartphones",
            brand="Apple",
            price_min=None,
            price_max=None,
            providers=[],
            words_to_avoid=[],
            enable_llm_validation=False,
            is_active=True,
        )
        listing = listing_factory(title="Random title", brand=None)
        # Brand is in search query, so filter is skipped
        assert _matches_brand(snapshot, listing) is True

    def test_explicit_brand_mismatch_is_not_bypassed_by_search_query(self, listing_factory) -> None:
        snapshot = ProductTemplateSnapshot(
            product_id="00000000-0000-0000-0000-000000000001",
            name="Apple iPhone 14",
            description=None,
            search_query="Apple iPhone 14",
            category_id="00000000-0000-0000-0000-000000000010",
            category_name="Smartphones",
            brand="Apple",
            price_min=None,
            price_max=None,
            providers=[],
            words_to_avoid=[],
            enable_llm_validation=False,
            is_active=True,
        )
        listing = listing_factory(title="Samsung Galaxy S24", brand="Samsung")

        assert _matches_brand(snapshot, listing) is False

    def test_no_brand_configured(self, listing_factory) -> None:
        snapshot = ProductTemplateSnapshot(
            product_id="00000000-0000-0000-0000-000000000001",
            name="Test",
            description=None,
            search_query="test query",
            category_id="00000000-0000-0000-0000-000000000010",
            category_name=None,
            brand=None,
            price_min=None,
            price_max=None,
            providers=[],
            words_to_avoid=[],
            enable_llm_validation=False,
            is_active=True,
        )
        listing = listing_factory()
        assert _matches_brand(snapshot, listing) is True

    def test_brand_mismatch(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(title="Samsung Galaxy S24", brand="Samsung")
        # sample_snapshot has brand="Apple" and search_query doesn't contain "Apple"
        # Wait - sample_snapshot search_query is "iPhone 14 Pro 128GB", no "Apple"
        assert _matches_brand(sample_snapshot, listing) is False


class TestMatchesWordsToAvoid:
    def test_no_bad_words(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(title="iPhone 14 Pro 128GB excellent état")
        assert _matches_words_to_avoid(sample_snapshot, listing) is True

    def test_contains_bad_word(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(title="Coque iPhone 14 Pro protection")
        assert _matches_words_to_avoid(sample_snapshot, listing) is False

    def test_empty_avoid_list(self, listing_factory) -> None:
        snapshot = ProductTemplateSnapshot(
            product_id="00000000-0000-0000-0000-000000000001",
            name="Test",
            description=None,
            search_query="test",
            category_id="00000000-0000-0000-0000-000000000010",
            category_name=None,
            brand=None,
            price_min=None,
            price_max=None,
            providers=[],
            words_to_avoid=[],
            enable_llm_validation=False,
            is_active=True,
        )
        listing = listing_factory(title="anything goes here")
        assert _matches_words_to_avoid(snapshot, listing) is True

    def test_case_insensitive(self, sample_snapshot, listing_factory) -> None:
        listing = listing_factory(title="COQUE iPhone 14 Pro")
        assert _matches_words_to_avoid(sample_snapshot, listing) is False


@pytest.mark.asyncio
async def test_pipeline_keeps_device_bundle_and_rejects_accessory(listing_factory) -> None:
    snapshot = ProductTemplateSnapshot(
        product_id="00000000-0000-0000-0000-000000000001",
        name="Sony PlayStation 5",
        description=None,
        search_query="PlayStation 5",
        category_id="00000000-0000-0000-0000-000000000010",
        category_name="Gaming",
        brand="Sony",
        price_min=None,
        price_max=None,
        providers=[],
        words_to_avoid=[],
        enable_llm_validation=False,
        is_active=True,
    )
    bundle = listing_factory(listing_id="bundle", title="PS5 avec 2 manettes")
    accessory = listing_factory(listing_id="accessory", title="Manette DualSense pour PS5")

    filtered, stats, llm_results, _ = await filter_listings_multi_stage(
        snapshot, [bundle, accessory]
    )

    assert filtered == [bundle]
    assert stats.passed_relevance == 1
    assert stats.rejected_relevance == 1
    assert stats.relevance_counts == {"device_bundle": 1, "accessory": 1}
    assert llm_results == {}


@pytest.mark.asyncio
async def test_llm_failure_does_not_pass_listing(sample_snapshot, listing_factory) -> None:
    snapshot = ProductTemplateSnapshot(
        **{**sample_snapshot.__dict__, "enable_llm_validation": True}
    )
    product_template = type(
        "Product",
        (),
        {"enable_llm_validation": True, "words_to_avoid": []},
    )()
    listing = listing_factory(title="Apple iPhone 14 Pro 128GB")

    with (
        patch("ingestion.filtering.capture_listing_screenshot", return_value=None),
        patch("ingestion.filtering.assess_listing_relevance", side_effect=RuntimeError("offline")),
    ):
        filtered, stats, llm_results, _ = await filter_listings_multi_stage(
            snapshot,
            [listing],
            product_template=product_template,
            enable_llm=True,
        )

    assert filtered == []
    assert stats.rejected_llm == 1
    assert llm_results[listing.listing_id]["classification"] == "uncertain"


@pytest.mark.asyncio
async def test_vision_shadow_keeps_legacy_llm_gate(sample_snapshot, listing_factory, monkeypatch):
    from libs.common.settings import settings

    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_shadow_mode", True)
    product = type("Product", (), {"enable_llm_validation": True})()
    listing = listing_factory(title="Apple iPhone 14 Pro 128GB")
    with (
        patch("ingestion.filtering.capture_listing_screenshot", return_value=None),
        patch("ingestion.filtering.assess_listing_relevance", return_value={"is_relevant": False}),
    ):
        kept, _, _, _ = await filter_listings_multi_stage(sample_snapshot, [listing], product, True)
    assert kept == []


@pytest.mark.asyncio
async def test_vision_keeps_ambiguous_active_candidate_but_not_sold(
    sample_snapshot, listing_factory, monkeypatch
):
    from libs.common.settings import settings

    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "vision_shadow_mode", False)
    active = listing_factory(title="Apple iPhone en bon état")
    sold = listing_factory(title="Apple iPhone en bon état", is_sold=True, listing_id="sold")
    with patch("ingestion.filtering.assess_listing_relevance") as old_llm:
        kept, _, results, _ = await filter_listings_multi_stage(sample_snapshot, [active, sold])
    assert kept == [active]
    assert results[active.listing_id]["status"] == "pending"
    old_llm.assert_not_called()

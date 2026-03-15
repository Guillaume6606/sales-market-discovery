from ingestion.validation import validate_listing, validate_listings


class TestValidateListing:
    def test_valid_listing_passes(self, sample_listing) -> None:
        assert validate_listing(sample_listing) is None

    def test_price_zero_rejected(self, listing_factory) -> None:
        listing = listing_factory(price=0.0)
        assert validate_listing(listing) == "price_non_positive"

    def test_price_negative_rejected(self, listing_factory) -> None:
        listing = listing_factory(price=-10.0)
        assert validate_listing(listing) == "price_non_positive"

    def test_price_too_high_rejected(self, listing_factory) -> None:
        listing = listing_factory(price=60000.0)
        assert validate_listing(listing) == "price_too_high"

    def test_none_price_ok(self, listing_factory) -> None:
        listing = listing_factory(price=None)
        assert validate_listing(listing) is None

    def test_empty_title_rejected(self, listing_factory) -> None:
        listing = listing_factory(title="")
        assert validate_listing(listing) == "empty_title"

    def test_whitespace_title_rejected(self, listing_factory) -> None:
        listing = listing_factory(title="   ")
        assert validate_listing(listing) == "empty_title"


class TestValidateListings:
    def test_batch_counts_correct(self, listing_factory) -> None:
        listings = [
            listing_factory(listing_id="1", price=100.0, title="Good item"),
            listing_factory(listing_id="2", price=0.0, title="Bad price"),
            listing_factory(listing_id="3", price=500.0, title=""),
            listing_factory(listing_id="4", price=200.0, title="Another good"),
        ]
        valid, stats = validate_listings(listings)
        assert len(valid) == 2
        assert stats.total == 4
        assert stats.passed == 2
        assert stats.rejected_price == 1
        assert stats.rejected_title == 1
        assert stats.rejected_reasons["price_non_positive"] == 1
        assert stats.rejected_reasons["empty_title"] == 1

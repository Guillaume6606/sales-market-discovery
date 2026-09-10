from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from ingestion.ingestion import _upsert_listing
from libs.common.models import ListingObservation, ProductTemplate


def test_repeat_fetch_preserves_first_observation_and_records_new_price(listing_factory):
    first = datetime.now(UTC) - timedelta(days=3)
    existing = ListingObservation(
        obs_id=1,
        product_id="p",
        price=100,
        currency="EUR",
        is_sold=False,
        observed_at=first,
        last_seen_at=first,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = existing
    product = ProductTemplate(product_id="p")
    assert _upsert_listing(db, product, listing_factory(price=90))
    assert existing.observed_at == first
    assert existing.price == 90
    assert any(
        type(call.args[0]).__name__ == "ListingObservationEvent" for call in db.add.call_args_list
    )


def test_sold_flag_cannot_override_asking_evidence(listing_factory):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert not _upsert_listing(
        db, ProductTemplate(product_id="p"), listing_factory(is_sold=False), force_is_sold=True
    )


def test_identical_decimal_price_does_not_create_another_event(listing_factory):
    from decimal import Decimal

    listing = listing_factory(price=90.10, shipping_cost=4.10)
    existing = ListingObservation(
        obs_id=1,
        product_id="p",
        price=Decimal("90.10"),
        shipping_cost=Decimal("4.10"),
        currency=listing.currency,
        title=listing.title,
        condition=listing.condition_raw,
        url=listing.url,
        evidence_type=listing.evidence_type,
        is_sold=listing.is_sold,
        observed_at=listing.observed_at,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = existing
    assert _upsert_listing(db, ProductTemplate(product_id="p"), listing)
    db.add.assert_not_called()

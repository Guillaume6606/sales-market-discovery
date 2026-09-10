from datetime import UTC, datetime, timedelta

from backend.main import product_price_history
from libs.common.models import ListingObservation, ListingObservationEvent


def test_price_history_uses_recorded_changes_without_rewriting_old_prices(
    integration_db, seed_product
):
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    listing = ListingObservation(
        product_id=seed_product,
        source="ebay",
        listing_id="history",
        price=50,
        currency="EUR",
        is_sold=False,
        observed_at=yesterday,
    )
    integration_db.add(listing)
    integration_db.flush()
    integration_db.add_all(
        [
            ListingObservationEvent(
                obs_id=listing.obs_id,
                recorded_at=yesterday,
                price=100,
                currency="EUR",
                is_sold=False,
                evidence_type="asking",
            ),
            ListingObservationEvent(
                obs_id=listing.obs_id,
                recorded_at=now,
                price=50,
                currency="EUR",
                is_sold=False,
                evidence_type="asking",
            ),
        ]
    )
    integration_db.commit()
    rows = product_price_history(seed_product, 7, integration_db)["active_history"]
    assert [(row["date"], row["avg_price"]) for row in rows] == [
        (str(yesterday.date()), 100),
        (str(now.date()), 50),
    ]

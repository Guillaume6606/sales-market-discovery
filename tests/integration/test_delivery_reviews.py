"""PostgreSQL regressions for delivery review validity and schema migration."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from ingestion.ingestion import _upsert_listing
from ingestion.valuation import _effective_observation, _get_listing_review, _listing_reasons
from libs.common.models import Listing, ListingObservation, ProductTemplate
from libs.common.valuation_models import ValuationListingReview


def _seed_review(
    db: Session, product_id: str
) -> tuple[Listing, ListingObservation, ValuationListingReview]:
    now = datetime.now(UTC)
    listing = Listing(
        source="vinted",
        listing_id="delivery-review",
        title="iPhone 14 Pro 128GB",
        price=300,
        currency="EUR",
        condition_raw="good",
        condition_norm="good",
        location=None,
        seller_rating=None,
        shipping_cost=5,
        observed_at=now,
        is_sold=False,
        url="https://example.com/original",
    )
    product = db.get(ProductTemplate, UUID(product_id))
    assert _upsert_listing(db, product, listing)
    db.commit()
    observation = db.query(ListingObservation).filter_by(listing_id=listing.listing_id).one()
    review = ValuationListingReview(
        obs_id=observation.obs_id,
        reviewed_title=listing.title,
        reviewed_condition="good",
        reviewed_shipping_cost_eur=5,
        reviewed_delivery_to_france=True,
        delivery_evidence="Checkout to France verified",
        reviewed_by="integration-test",
        reviewed_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=1),
        notes="test",
        raw_title=listing.title,
        raw_price_eur=300,
        raw_condition="good",
        raw_shipping_cost_eur=5,
        raw_delivery_to_france=None,
        raw_delivery_evidence=None,
        raw_url=listing.url,
        is_active=True,
    )
    db.add(review)
    db.commit()
    assert _get_listing_review(db, observation, now) is not None
    return listing, observation, review


def test_expired_delivery_review_no_longer_confirms_france(
    integration_db: Session, seed_product: str
) -> None:
    _, observation, review = _seed_review(integration_db, seed_product)
    at_expiry = review.expires_at
    assert (
        _get_listing_review(integration_db, observation, at_expiry - timedelta(microseconds=1))
        is not None
    )
    assert _get_listing_review(integration_db, observation, at_expiry) is None
    effective = _effective_observation(
        observation, _get_listing_review(integration_db, observation, at_expiry)
    )
    assert "france_delivery_unconfirmed" in _listing_reasons(effective, at_expiry)


@pytest.mark.parametrize(
    "change",
    [
        {"delivery_to_france": False, "delivery_evidence": "Pickup only"},
        {"url": "https://example.com/replacement"},
    ],
)
def test_delivery_review_cannot_revive_after_changed_listing_reverts(
    integration_db: Session, seed_product: str, change: dict
) -> None:
    listing, observation, review = _seed_review(integration_db, seed_product)
    product = integration_db.get(ProductTemplate, UUID(seed_product))
    assert _upsert_listing(integration_db, product, listing.model_copy(update=change))
    integration_db.commit()
    integration_db.refresh(review)
    assert review.is_active is False
    assert _get_listing_review(integration_db, observation, datetime.now(UTC)) is None
    assert _upsert_listing(integration_db, product, listing)
    integration_db.commit()
    integration_db.refresh(review)
    assert review.is_active is False
    assert _get_listing_review(integration_db, observation, datetime.now(UTC)) is None


def test_delivery_migration_upgrade_downgrade_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required")
    schema = f"test_migration_{uuid4().hex}"
    admin = create_engine(database_url)
    scoped_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    scoped = create_engine(scoped_url)
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        assert {"delivery_to_france", "delivery_evidence"} <= {
            c["name"] for c in inspect(scoped).get_columns("listing_observation")
        }
        assert "raw_url" in {
            c["name"] for c in inspect(scoped).get_columns("valuation_listing_review")
        }
        command.downgrade(config, "0010_trade_ledger")
        assert "delivery_to_france" not in {
            c["name"] for c in inspect(scoped).get_columns("listing_observation")
        }
        assert "raw_url" not in {
            c["name"] for c in inspect(scoped).get_columns("valuation_listing_review")
        }
        command.upgrade(config, "head")
        assert "raw_url" in {
            c["name"] for c in inspect(scoped).get_columns("valuation_listing_review")
        }
    finally:
        scoped.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()

"""Integration test fixtures with SQLite in-memory database.

Uses raw SQL for seeding to avoid SQLAlchemy UUID bind processor issues,
while the computation functions under test use the ORM session.
The key insight: we patch the metadata column types ONCE globally, and
importantly, we clear SQLAlchemy's type compilation caches so the patched
types take effect even if unit tests ran first.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import UUID as SA_UUID
from sqlalchemy import Integer, String, Text, create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import ARRAY, BigInteger

from libs.common.models import Base

# Track whether we've already patched metadata
_metadata_patched = False


def _patch_metadata_for_sqlite():
    """Patch Base.metadata column types for SQLite compatibility.

    This mutates the global metadata in-place. Safe because integration tests
    run this process and these types won't be used with Postgres afterward.
    """
    global _metadata_patched
    if _metadata_patched:
        return
    _metadata_patched = True

    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, ARRAY):
                col.type = Text()
            elif isinstance(col.type, SA_UUID):
                col.type = String(36)
            elif isinstance(col.type, BigInteger):
                col.type = Integer()

            # Remove Postgres-only server defaults
            if col.server_default is not None:
                try:
                    sd_text = str(col.server_default.arg)
                except Exception:
                    sd_text = ""
                if "gen_random_uuid" in sd_text or "now" in sd_text:
                    col.server_default = None

        # Clear cached type processors so new types take effect
        for col in table.columns:
            if hasattr(col.type, "_literal_processor"):
                col.type._literal_processor = None


# Patch metadata at import time (before any fixtures run)
_patch_metadata_for_sqlite()


@pytest.fixture()
def integration_db():
    """In-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def seed_category(integration_db):
    """Seed a category and return its ID."""
    cat_id = str(uuid.uuid4())
    integration_db.execute(
        text("INSERT INTO category (category_id, name) VALUES (:id, :name)"),
        {"id": cat_id, "name": "Electronics"},
    )
    integration_db.commit()
    return cat_id


@pytest.fixture()
def seed_product(integration_db, seed_category):
    """Seed a product template and return its ID."""
    product_id = str(uuid.uuid4())
    integration_db.execute(
        text(
            "INSERT INTO product_template "
            "(product_id, name, search_query, category_id, brand, is_active) "
            "VALUES (:pid, :name, :sq, :cid, :brand, 1)"
        ),
        {
            "pid": product_id,
            "name": "iPhone 14 Pro 128GB",
            "sq": "iPhone 14 Pro 128GB",
            "cid": seed_category,
            "brand": "Apple",
        },
    )
    integration_db.commit()
    return product_id


@pytest.fixture()
def seed_sold_observations(integration_db, seed_product):
    """Seed 20 sold listing observations with known prices."""
    base_price = 700.0
    for i in range(20):
        integration_db.execute(
            text(
                "INSERT INTO listing_observation "
                "(product_id, source, listing_id, title, price, currency, "
                "condition, is_sold, observed_at) "
                "VALUES (:pid, :src, :lid, :title, :price, :cur, :cond, 1, :obs_at)"
            ),
            {
                "pid": seed_product,
                "src": "ebay",
                "lid": f"sold-{i}",
                "title": f"iPhone 14 Pro #{i}",
                "price": base_price + (i * 5),
                "cur": "EUR",
                "cond": "Used",
                "obs_at": (datetime.now(UTC) - timedelta(days=i)).isoformat(),
            },
        )
    integration_db.commit()

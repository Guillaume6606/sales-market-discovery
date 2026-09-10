"""PostgreSQL integration test fixtures."""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from libs.common.models import Base


def _register_model_modules() -> None:
    """Load standalone model modules so their tables join the shared metadata."""
    for module_name in ("libs.common.valuation_models", "libs.common.trade_models"):
        importlib.import_module(module_name)


@pytest.fixture()
def integration_db() -> Generator[Session, None, None]:
    """Create a real PostgreSQL schema isolated from every other test run."""
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL")

    _register_model_modules()
    schema_name = f"test_{uuid.uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    schema_created = False
    test_engine: Engine | None = None
    session: Session | None = None

    try:
        with admin_engine.begin() as connection:
            connection.execute(CreateSchema(schema_name))
        schema_created = True

        test_engine = create_engine(
            database_url,
            connect_args={"options": f"-csearch_path={schema_name}"},
            pool_pre_ping=True,
        )
        Base.metadata.create_all(bind=test_engine)
        session = sessionmaker(bind=test_engine)()
        yield session
    finally:
        if session is not None:
            session.close()
        if test_engine is not None:
            test_engine.dispose()
        if schema_created:
            with admin_engine.begin() as connection:
                connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        admin_engine.dispose()


@pytest.fixture()
def seed_category(integration_db: Session) -> str:
    """Seed a category and return its ID."""
    category_id = str(uuid.uuid4())
    integration_db.execute(
        text("INSERT INTO category (category_id, name) VALUES (:id, :name)"),
        {"id": category_id, "name": "Electronics"},
    )
    integration_db.commit()
    return category_id


@pytest.fixture()
def seed_product(integration_db: Session, seed_category: str) -> str:
    """Seed a product template and return its ID."""
    product_id = str(uuid.uuid4())
    integration_db.execute(
        text(
            "INSERT INTO product_template "
            "(product_id, name, search_query, category_id, brand, is_active) "
            "VALUES (:pid, :name, :sq, :cid, :brand, true)"
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
def seed_sold_observations(integration_db: Session, seed_product: str) -> list[int]:
    """Seed 20 verified sold listing observations with known prices."""
    observation_ids = []
    base_price = 700.0
    for index in range(20):
        observation_id = integration_db.execute(
            text(
                "INSERT INTO listing_observation "
                "(product_id, source, listing_id, title, price, currency, condition, "
                "is_sold, evidence_type, observed_at) "
                "VALUES (:pid, :src, :lid, :title, :price, :cur, :cond, true, "
                ":evidence_type, :observed_at) RETURNING obs_id"
            ),
            {
                "pid": seed_product,
                "src": "ebay",
                "lid": f"sold-{index}",
                "title": f"iPhone 14 Pro #{index}",
                "price": base_price + (index * 5),
                "cur": "EUR",
                "cond": "Used",
                "evidence_type": "verified_sale",
                "observed_at": datetime.now(UTC) - timedelta(days=index),
            },
        ).scalar_one()
        observation_ids.append(observation_id)
    integration_db.commit()
    return observation_ids

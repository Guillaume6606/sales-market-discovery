"""Shared fixtures for the smoke test suite.

These tests exercise live infrastructure (Postgres, Redis, connectors, ingestion
pipeline) and are intended to run inside the Docker network where all services
are reachable by their service names.
"""

import asyncio
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy.orm import Session

from libs.common.db import SessionLocal
from libs.common.models import ProductTemplate


@pytest.fixture(scope="session")
def db_session() -> Generator[Session, None, None]:
    """Real Postgres session for smoke tests."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def known_product(db_session: Session) -> ProductTemplate:
    """Load the first active product from the DB. Skip if none exists."""
    product = db_session.query(ProductTemplate).filter(ProductTemplate.is_active.is_(True)).first()
    if product is None:
        pytest.skip("No active products configured in the database")
    return product


@pytest.fixture(scope="session")
def known_product_id(known_product: ProductTemplate) -> str:
    """Return the product_id of the first active product as a string."""
    return str(known_product.product_id)


@pytest.fixture(scope="session")
def ingestion_result(known_product_id: str) -> dict[str, Any]:
    """Run full ingestion once for the session, shared across pipeline and data quality tests.

    Uses a small limits dict so the smoke run is fast and does not hammer the
    external marketplaces.
    """
    from ingestion.ingestion import run_full_ingestion

    return asyncio.run(
        run_full_ingestion(
            product_id=known_product_id,
            limits={
                "ebay_sold": 5,
                "ebay_listings": 5,
                "leboncoin_listings": 5,
                "leboncoin_sold": 5,
                "vinted_listings": 5,
            },
        )
    )


@pytest.fixture(scope="session")
def pmn_result(
    db_session: Session,
    known_product_id: str,
    ingestion_result: dict[str, Any],  # noqa: ARG001
) -> dict[str, Any]:
    """Compute PMN once for the session, shared across PMN tests."""
    from ingestion.computation import compute_pmn_for_product

    return compute_pmn_for_product(known_product_id, db_session)

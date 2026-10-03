"""PostgreSQL regression for clearing legacy seller proxies without losing listings."""

import os
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema


def test_seller_signal_migration_preserves_listing_and_financial_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required")
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL")
    schema = f"test_seller_migration_{uuid4().hex}"
    admin = create_engine(database_url)
    scoped_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    scoped = create_engine(scoped_url)
    snapshot = text(
        "SELECT to_jsonb(o) AS observation, to_jsonb(d) AS detail "
        "FROM listing_observation o JOIN listing_detail d USING (obs_id) ORDER BY o.obs_id"
    )
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
        config = Config("alembic.ini")
        command.upgrade(config, "0012_listing_vision")
        with scoped.begin() as connection:
            for index, (source, rating, transactions) in enumerate(
                [
                    ("ebay", Decimal("0"), 0),
                    ("ebay", Decimal("5"), 5),
                    ("ebay", Decimal("1500"), 1500),
                    ("ebay", None, None),
                    ("leboncoin", Decimal("4.2"), 12),
                    ("vinted", Decimal("4.8"), 80),
                    ("cashconverters", Decimal("4.9"), 200),
                ]
            ):
                obs_id = connection.execute(
                    text(
                        "INSERT INTO listing_observation "
                        "(source, listing_id, title, price, currency, shipping_cost, "
                        "seller_rating, is_sold, evidence_type, delivery_to_france) "
                        "VALUES (:source, :listing_id, 'Phone', 123.45, 'EUR', 6.78, "
                        ":rating, false, 'asking', true) RETURNING obs_id"
                    ),
                    {"source": source, "listing_id": str(index), "rating": rating},
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO listing_detail "
                        "(obs_id, description, seller_transaction_count, seller_account_age_days) "
                        "VALUES (:obs_id, 'Preserve listing detail', :transactions, 400)"
                    ),
                    {"obs_id": obs_id, "transactions": transactions},
                )
            before = [dict(row) for row in connection.execute(snapshot).mappings()]
        expected = deepcopy(before)
        for row in expected:
            source = row["observation"]["source"]
            if source == "ebay":
                row["observation"]["seller_rating"] = None
            if source in {"ebay", "leboncoin", "vinted"}:
                row["detail"]["seller_transaction_count"] = None

        command.upgrade(config, "0013_seller_signal_semantics")
        with scoped.connect() as connection:
            assert [dict(row) for row in connection.execute(snapshot).mappings()] == expected
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0013_seller_signal_semantics")

        command.downgrade(config, "0012_listing_vision")
        with scoped.connect() as connection:
            assert [dict(row) for row in connection.execute(snapshot).mappings()] == expected
    finally:
        scoped.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        admin.dispose()

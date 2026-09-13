"""Ingestion health: no_data is reported separately from success, errors are grouped."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.routers.health import summarize_outcomes
from libs.common.db import get_db


@pytest.fixture()
def db_session():
    return MagicMock()


@pytest.fixture()
def client(db_session):
    from backend.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestSummarizeOutcomes:
    def test_no_data_is_not_counted_as_success(self):
        row = MagicMock(total=10, successes=4, no_data=4, errors=2)

        assert summarize_outcomes(row) == {
            "runs": 10,
            "success_rate": 0.4,
            "no_data_rate": 0.4,
            "error_rate": 0.2,
        }

    def test_empty_window_yields_none_rates(self):
        assert summarize_outcomes(None) == {
            "runs": 0,
            "success_rate": None,
            "no_data_rate": None,
            "error_rate": None,
        }


class TestIngestionErrors:
    def test_groups_errors_by_source_and_message(self, client, db_session):
        last_seen = datetime.now(UTC)
        rows = [("vinted", "Vinted API search failed: HTTP 403", 12, last_seen)]
        chain = db_session.query.return_value.filter.return_value.group_by.return_value
        chain.order_by.return_value.limit.return_value.all.return_value = rows

        response = client.get("/health/ingestion/errors?days=7")

        assert response.status_code == 200
        assert response.json() == [
            {
                "source": "vinted",
                "error_message": "Vinted API search failed: HTTP 403",
                "count": 12,
                "last_seen_at": last_seen.isoformat(),
            }
        ]

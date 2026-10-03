"""Tests for health endpoints."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from libs.common.db import get_db


@pytest.fixture()
def db_session():
    """Create a mock DB session."""
    return MagicMock()


@pytest.fixture()
def client(db_session):
    """Create test client with overridden DB dependency."""
    from backend.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestIngestionHealth:
    def test_returns_list(self, client, db_session):
        """GET /health/ingestion returns a list."""
        # No sources in DB → empty list
        db_session.query.return_value.filter.return_value.distinct.return_value.all.return_value = []
        response = client.get("/health/ingestion")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestProductHealth:
    def test_returns_list(self, client, db_session):
        """GET /health/products returns a list."""
        db_session.query.return_value.filter.return_value.all.return_value = []
        response = client.get("/health/products")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_stale_flagging(self, client, db_session):
        """Products with old last_ingested_at are flagged stale."""
        product = MagicMock()
        product.product_id = uuid4()
        product.name = "Test Product"
        product.is_active = True
        product.last_ingested_at = datetime.now(UTC) - timedelta(hours=48)

        db_session.query.return_value.filter.return_value.all.return_value = [product]
        response = client.get("/health/products")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["is_stale"] is True

    def test_fresh_product_not_stale(self, client, db_session):
        """Products with recent ingestion are not flagged stale."""
        product = MagicMock()
        product.product_id = uuid4()
        product.name = "Fresh Product"
        product.is_active = True
        product.last_ingested_at = datetime.now(UTC) - timedelta(hours=1)

        db_session.query.return_value.filter.return_value.all.return_value = [product]
        response = client.get("/health/products")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["is_stale"] is False


class TestOverview:
    @patch("backend.routers.health.compute_precision_summary", return_value={"precision": None})
    def test_health_threshold_uses_unrounded_rate(self, mock_precision, client, db_session):
        db_session.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            ("vinted",)
        ]
        db_session.query.return_value.filter.return_value.all.return_value = []
        db_session.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
        with patch(
            "backend.routers.health._fetch_outcomes",
            return_value=SimpleNamespace(total=200, successes=159, no_data=41, errors=0),
        ):
            response = client.get("/health/overview")
        assert response.json()["connectors"][0]["status"] == "yellow"

    @patch("backend.routers.health.compute_precision_summary", return_value={"precision": None})
    def test_empty_fetches_are_not_healthy(self, mock_precision, client, db_session):
        datetime.now(UTC)
        db_session.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            ("vinted",)
        ]
        db_session.query.return_value.filter.return_value.all.return_value = []
        db_session.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
        with patch(
            "backend.routers.health._fetch_outcomes",
            return_value=SimpleNamespace(total=3, successes=0, no_data=3, errors=0),
        ):
            response = client.get("/health/overview")
        assert response.status_code == 200
        connector = response.json()["connectors"][0]
        assert connector["status"] != "green"
        assert connector["no_data_runs_24h"] == 3
        assert connector["no_data_rate_24h"] == 1.0

    @patch("backend.routers.health.compute_precision_summary", return_value={"precision": None})
    def test_returns_expected_keys(self, mock_precision, client, db_session):
        """GET /health/overview returns expected structure."""
        # Mock sources query
        db_session.query.return_value.filter.return_value.distinct.return_value.all.return_value = []
        # Mock products query
        db_session.query.return_value.filter.return_value.all.return_value = []
        # Mock recent runs
        db_session.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
        response = client.get("/health/overview")
        assert response.status_code == 200
        data = response.json()
        assert "system_status" in data
        assert "connectors" in data
        assert "stale_product_count" in data
        assert "recent_runs" in data
        assert "precision" in data

    @patch("backend.routers.health.compute_precision_summary", return_value={"precision": None})
    def test_unknown_status_without_runs(self, mock_precision, client, db_session):
        """Ingestion status is unknown without observed runs."""
        db_session.query.return_value.filter.return_value.distinct.return_value.all.return_value = []
        db_session.query.return_value.filter.return_value.all.return_value = []
        db_session.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
        response = client.get("/health/overview")
        data = response.json()
        assert data["system_status"] == "gray"
        assert data["stale_product_count"] == 0

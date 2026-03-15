"""Tests for health endpoints."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
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
    def test_returns_expected_keys(self, client, db_session):
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

    def test_green_status_when_healthy(self, client, db_session):
        """System status is green when no stale products and no red connectors."""
        db_session.query.return_value.filter.return_value.distinct.return_value.all.return_value = []
        db_session.query.return_value.filter.return_value.all.return_value = []
        db_session.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
        response = client.get("/health/overview")
        data = response.json()
        assert data["system_status"] == "green"
        assert data["stale_product_count"] == 0

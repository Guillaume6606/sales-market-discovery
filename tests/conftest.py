from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ingestion.schemas import ProductTemplateSnapshot
from libs.common.models import Listing


@pytest.fixture(autouse=True)
def offline_connector_cooldown(monkeypatch):
    from ingestion.connectors import leboncoin_api, vinted_api

    redis = AsyncMock()
    redis.exists.return_value = 0
    monkeypatch.setattr(vinted_api, "_get_redis", lambda: redis)
    monkeypatch.setattr(vinted_api.VintedAPIConnector, "_cooldown_until", 0.0)
    monkeypatch.setattr(leboncoin_api, "_get_redis", lambda: redis)
    monkeypatch.setattr(leboncoin_api.LeBonCoinAPIConnector, "_cooldown_until", 0.0)


@pytest.fixture
def sample_listing() -> Listing:
    return Listing(
        source="ebay",
        listing_id="test-123",
        title="Apple iPhone 14 Pro 128GB Space Black",
        price=750.0,
        currency="EUR",
        condition_raw="Used",
        condition_norm="good",
        location="Paris, France",
        seller_rating=4.8,
        shipping_cost=5.0,
        observed_at=datetime.now(UTC),
        is_sold=False,
        url="https://example.com/listing/test-123",
        brand="Apple",
    )


@pytest.fixture
def sample_snapshot() -> ProductTemplateSnapshot:
    return ProductTemplateSnapshot(
        product_id="00000000-0000-0000-0000-000000000001",
        name="iPhone 14 Pro 128GB",
        description="Apple iPhone 14 Pro 128GB",
        search_query="iPhone 14 Pro 128GB",
        category_id="00000000-0000-0000-0000-000000000010",
        category_name="Smartphones",
        brand="Apple",
        price_min=400.0,
        price_max=1200.0,
        providers=["ebay", "leboncoin", "vinted"],
        words_to_avoid=["coque", "protection", "vitre"],
        enable_llm_validation=False,
        is_active=True,
    )


@pytest.fixture
def listing_factory():
    def _factory(**overrides) -> Listing:
        defaults = {
            "source": "ebay",
            "listing_id": "factory-001",
            "title": "Test Product Item",
            "price": 500.0,
            "currency": "EUR",
            "condition_raw": "Used",
            "condition_norm": "good",
            "location": "Paris",
            "seller_rating": 4.5,
            "shipping_cost": 0.0,
            "observed_at": datetime.now(UTC),
            "is_sold": False,
            "url": "https://example.com/listing/factory-001",
            "brand": None,
        }
        defaults.update(overrides)
        return Listing(**defaults)

    return _factory

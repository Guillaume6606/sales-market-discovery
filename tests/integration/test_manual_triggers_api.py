"""Manual ingestion/enrichment trigger contracts against real PostgreSQL."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import backend.main as backend_main


@pytest.fixture()
def fake_queue(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    enqueue = AsyncMock(return_value=SimpleNamespace(job_id="job-1"))
    monkeypatch.setattr(backend_main, "arq_pool", SimpleNamespace(enqueue_job=enqueue))
    return enqueue


def _restrict_providers(db: Session, product_id: str, providers: list[str]) -> None:
    from libs.common.models import ProductTemplate

    db.get(ProductTemplate, product_id).providers = providers
    db.commit()


def test_ingestion_trigger_uses_providers_selected_in_query(
    client: TestClient, integration_db: Session, seed_product: str, fake_queue: AsyncMock
) -> None:
    _restrict_providers(integration_db, seed_product, ["ebay", "leboncoin"])

    response = client.post(
        "/ingestion/trigger",
        params={"product_id": seed_product, "sold_limit": 0, "sources": ["leboncoin"]},
    )

    assert response.status_code == 200
    assert fake_queue.await_args.kwargs["sources"] == ["leboncoin"]


def test_ingestion_trigger_refuses_providers_not_allowed_for_product(
    client: TestClient, integration_db: Session, seed_product: str, fake_queue: AsyncMock
) -> None:
    _restrict_providers(integration_db, seed_product, ["ebay"])

    response = client.post(
        "/ingestion/trigger", params={"product_id": seed_product, "sources": ["vinted"]}
    )

    assert response.status_code == 422
    fake_queue.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "path", "params"),
    [
        ("post", "/ingestion/trigger", {"product_id": "{product}"}),
        ("post", "/enrichment/trigger", {}),
    ],
)
def test_manual_triggers_report_unavailable_queue_as_an_error(
    client: TestClient,
    seed_product: str,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    params: dict[str, Any],
) -> None:
    monkeypatch.setattr(backend_main, "arq_pool", None)
    resolved = {key: value.format(product=seed_product) for key, value in params.items()}

    response = client.request(method, path, params=resolved)

    assert response.status_code == 503

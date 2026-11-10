from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from backend.routers import health
from libs.common.db import get_db
from libs.common.settings import settings

GREEN_OUTCOMES = SimpleNamespace(total=10, successes=10, no_data=0, errors=0)


@pytest.fixture()
def db_session() -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
        ("ebay",),
        ("leboncoin",),
    ]
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
    return db


@pytest.fixture()
def client(db_session: MagicMock):
    from backend.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _overview(client: TestClient) -> dict:
    with (
        patch.object(health, "compute_precision_summary", return_value={}),
        patch.object(health, "_fetch_outcomes", return_value=GREEN_OUTCOMES),
    ):
        return client.get("/health/overview").json()


def test_single_worker_overview_does_not_query_heartbeats(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeats = Mock()
    monkeypatch.setattr(health, "worker_heartbeats", heartbeats)
    data = _overview(client)
    heartbeats.assert_not_called()
    assert data["system_status"] == "green"
    assert all("worker_online" not in c for c in data["connectors"])


def test_offline_remote_worker_is_reported_without_failing_primary_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "remote_providers", "leboncoin")
    monkeypatch.setattr(
        health, "worker_heartbeats", Mock(return_value={"primary": True, "remote": False})
    )
    data = _overview(client)
    connectors = {c["source"]: c for c in data["connectors"]}
    assert connectors["ebay"]["status"] == "green"
    assert connectors["ebay"]["worker_online"] is True
    assert connectors["leboncoin"]["status"] == "gray"
    assert connectors["leboncoin"]["worker"] == "remote"
    assert connectors["leboncoin"]["worker_online"] is False
    assert data["system_status"] == "yellow"
    assert data["workers"] == {"primary": True, "remote": False}


def test_online_remote_worker_keeps_green_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "remote_providers", "leboncoin")
    monkeypatch.setattr(
        health, "worker_heartbeats", Mock(return_value={"primary": True, "remote": True})
    )
    data = _overview(client)
    assert data["system_status"] == "green"
    assert {c["status"] for c in data["connectors"]} == {"green"}


def test_unknown_heartbeat_does_not_mark_worker_offline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "remote_providers", "leboncoin")
    monkeypatch.setattr(
        health, "worker_heartbeats", Mock(return_value={"primary": None, "remote": None})
    )
    data = _overview(client)
    assert data["system_status"] == "green"
    assert {c["worker_online"] for c in data["connectors"]} == {None}


def test_ingestion_health_names_the_worker_of_each_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from libs.common.models import IngestionRun

    engine = create_engine("sqlite://")
    IngestionRun.__table__.create(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        for source in ("ebay", "leboncoin"):
            db.add(
                IngestionRun(
                    run_id=uuid4(),
                    source=source,
                    status="success",
                    started_at=now,
                    finished_at=now,
                    listings_persisted=1,
                )
            )
        db.commit()
        assert all("worker" not in row for row in health.get_ingestion_health(db))
        monkeypatch.setattr(settings, "remote_providers", "leboncoin")
        monkeypatch.setattr(
            health, "worker_heartbeats", Mock(return_value={"primary": True, "remote": False})
        )
        rows = {row["source"]: row for row in health.get_ingestion_health(db)}
    assert rows["ebay"]["worker"] == "primary" and rows["ebay"]["worker_online"] is True
    assert rows["leboncoin"]["worker"] == "remote"
    assert rows["leboncoin"]["worker_online"] is False
    assert rows["leboncoin"]["success_rate_7d"] == 1.0


def test_workers_endpoint_lists_roles_queues_and_providers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "remote_providers", "leboncoin")
    monkeypatch.setattr(
        health, "worker_heartbeats", Mock(return_value={"primary": True, "remote": False})
    )
    data = client.get("/health/workers").json()
    assert data == [
        {
            "role": "primary",
            "queue": "arq:queue",
            "providers": ["ebay", "vinted", "cashconverters"],
            "online": True,
        },
        {
            "role": "remote",
            "queue": "arq:queue:remote",
            "providers": ["leboncoin"],
            "online": False,
        },
    ]


def test_heartbeats_read_each_expected_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "remote_providers", "leboncoin")
    redis_client = MagicMock()
    redis_client.__enter__.return_value = redis_client
    redis_client.exists.side_effect = lambda key: int(key == "worker:heartbeat")
    monkeypatch.setattr(health.redis.Redis, "from_url", Mock(return_value=redis_client))
    assert health.worker_heartbeats() == {"primary": True, "remote": False}


def test_heartbeats_are_unknown_when_redis_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_client = MagicMock()
    redis_client.__enter__.return_value = redis_client
    redis_client.exists.side_effect = health.redis.ConnectionError("down")
    monkeypatch.setattr(health.redis.Redis, "from_url", Mock(return_value=redis_client))
    assert health.worker_heartbeats() == {"primary": None}

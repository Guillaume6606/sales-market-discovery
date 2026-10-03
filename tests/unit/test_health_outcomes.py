from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.routers.health import _fetch_outcomes, _ingestion_status, get_ingestion_health
from libs.common.models import IngestionRun


def test_fetch_denominator_excludes_non_attempts() -> None:
    engine = create_engine("sqlite://")
    IngestionRun.__table__.create(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        for status in ["success", "no_data", "error", "unsupported", "running", "skipped"]:
            db.add(
                IngestionRun(
                    run_id=uuid4(),
                    source="vinted",
                    status=status,
                    started_at=now,
                    listings_persisted=2 if status == "success" else 0,
                )
            )
        db.commit()
        result = _fetch_outcomes(db, "vinted", now)
        assert result.total == 3
        assert result.successes == 1
        assert result.no_data == 1
        assert result.errors == 1
        assert _fetch_outcomes(db, "ebay", now).total == 0


def test_ingestion_health_reports_empty_and_failed_runs_separately() -> None:
    engine = create_engine("sqlite://")
    IngestionRun.__table__.create(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        for status, persisted in [
            ("success", 2),
            ("success", 0),
            ("no_data", 0),
            ("error", 0),
            ("running", 0),
        ]:
            db.add(
                IngestionRun(
                    run_id=uuid4(),
                    source="vinted",
                    status=status,
                    started_at=now,
                    finished_at=now,
                    listings_persisted=persisted,
                )
            )
        db.commit()
        result = get_ingestion_health(db)[0]
        for window in ("24h", "7d"):
            assert result[f"completed_runs_{window}"] == 4
            assert result[f"success_runs_{window}"] == 1
            assert result[f"no_data_runs_{window}"] == 2
            assert result[f"error_runs_{window}"] == 1
            assert result[f"success_rate_{window}"] == 0.25
            assert result[f"no_data_rate_{window}"] == 0.5
            assert result[f"error_rate_{window}"] == 0.25


def test_empty_fetch_has_no_success_timestamp() -> None:
    engine = create_engine("sqlite://")
    IngestionRun.__table__.create(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        db.add(
            IngestionRun(
                run_id=uuid4(),
                source="vinted",
                status="no_data",
                started_at=now,
                finished_at=now,
                listings_persisted=0,
            )
        )
        db.commit()
        result = get_ingestion_health(db)[0]
        assert result["last_success_at"] is None
        assert result["success_rate_24h"] == 0.0
        assert result["no_data_rate_24h"] == 1.0


@pytest.mark.parametrize(
    ("connectors", "stale", "expected"),
    [
        ([], 0, "gray"),
        ([{"status": "gray"}], 0, "gray"),
        ([{"status": "green"}], 0, "green"),
        ([{"status": "red"}], 0, "yellow"),
        ([{"status": "yellow"}], 0, "yellow"),
        ([{"status": "green"}], 1, "yellow"),
    ],
)
def test_ingestion_status_is_not_application_down(connectors, stale, expected) -> None:
    assert _ingestion_status(connectors, stale) == expected

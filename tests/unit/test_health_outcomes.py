from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.routers.health import _fetch_outcomes, _ingestion_status
from libs.common.models import IngestionRun


def test_fetch_denominator_excludes_non_attempts() -> None:
    engine = create_engine("sqlite://")
    IngestionRun.__table__.create(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        for status in ["success", "no_data", "error", "unsupported", "running", "skipped"]:
            db.add(IngestionRun(run_id=uuid4(), source="vinted", status=status, started_at=now))
        db.commit()
        result = _fetch_outcomes(db, "vinted", now)
        assert result.total == 3
        assert result.successes == 2
        assert _fetch_outcomes(db, "ebay", now).total == 0


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

"""Smoke tests — container health.

Verifies that every infrastructure dependency reachable from the test runner is
up, accepting connections, and at the expected schema revision.
"""

import subprocess

import httpx
import pytest
import redis as redis_lib
from sqlalchemy.orm import Session
from sqlalchemy.sql import text

from libs.common.settings import settings


def test_postgres_reachable(db_session: Session) -> None:
    """Postgres must accept connections and respond to a trivial query."""
    result = db_session.execute(text("SELECT 1"))
    row = result.fetchone()
    assert row is not None
    assert row[0] == 1


def test_redis_reachable() -> None:
    """Redis must be reachable and respond to PING."""
    client = redis_lib.from_url(settings.redis_url, socket_connect_timeout=5)
    try:
        response = client.ping()
        assert response is True
    finally:
        client.close()


def test_backend_health() -> None:
    """The FastAPI backend must return HTTP 200 on its /health endpoint."""
    response = httpx.get("http://backend:8000/health", timeout=5)
    assert response.status_code == 200


def test_alembic_migrations_current() -> None:
    """Alembic migrations must be up to date (no pending heads).

    Runs ``alembic check`` which exits 0 when the database is at the latest
    revision and non-zero when migrations are pending.  If the installed
    version of Alembic does not support ``check``, the test is skipped with an
    informative message.
    """
    result = subprocess.run(
        ["uv", "run", "alembic", "check"],
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if "No such command" in output:
        pytest.skip("alembic check not available in this Alembic version")
    if "script_location" in output or "No such file" in output:
        pytest.skip("alembic.ini not available in this container")
    assert result.returncode == 0, (
        f"Alembic migrations are not up to date.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

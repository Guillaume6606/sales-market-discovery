"""Connector data quality audit API endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from libs.common.db import SessionLocal
from libs.common.models import ConnectorAudit
from libs.common.settings import settings

router = APIRouter(prefix="/audit", tags=["audit"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/connectors/results")
def get_audit_results(
    connector: str | None = None,
    days: int = 7,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Get detailed audit results with per-field accuracy breakdown."""
    cutoff = datetime.now(UTC) - timedelta(days=days)

    query = db.query(ConnectorAudit).filter(ConnectorAudit.audited_at >= cutoff)
    if connector:
        query = query.filter(ConnectorAudit.source == connector)

    records = query.order_by(ConnectorAudit.audited_at.desc()).limit(500).all()

    if not records:
        return {"results": [], "accuracy": {}}

    from ingestion.audit import compute_connector_accuracy

    accuracy = compute_connector_accuracy(records)

    failures = []
    for r in records:
        if (
            r.accuracy_score is not None
            and float(r.accuracy_score) < settings.audit_accuracy_yellow
        ):
            failures.append(
                {
                    "obs_id": r.obs_id,
                    "source": r.source,
                    "accuracy": float(r.accuracy_score),
                    "audited_at": r.audited_at.isoformat(),
                    "notes": r.llm_response.get("notes") if r.llm_response else None,
                    "field_results": r.field_results,
                }
            )

    return {
        "period_days": days,
        "total_audited": len(records),
        "accuracy": accuracy,
        "recent_failures": failures[:20],
    }


@router.post("/connectors")
async def trigger_on_demand_audit(
    connector: str | None = None,
    sample_size: int = 20,
    product_id: str | None = None,
) -> dict[str, Any]:
    """Trigger an on-demand audit. Returns immediately with job status."""
    import redis as redis_lib

    r = redis_lib.from_url(settings.redis_url)
    lock_key = "audit:on_demand:running"
    if r.exists(lock_key):
        raise HTTPException(status_code=409, detail="An on-demand audit is already running")

    r.setex(lock_key, 1800, "running")

    try:
        from backend.main import enqueue_arq_job

        job = await enqueue_arq_job(
            "run_on_demand_audit",
            connector=connector,
            sample_size=sample_size,
            product_id=product_id,
        )
        return {"status": "enqueued", "job_id": str(job) if job else None}
    except Exception as exc:
        r.delete(lock_key)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

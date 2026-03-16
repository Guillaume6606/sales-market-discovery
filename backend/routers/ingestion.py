"""Ingestion run history endpoints."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc
from sqlalchemy.orm import Session

from libs.common.db import get_db
from libs.common.models import IngestionRun
from libs.common.utils import decimal_to_float as _decimal_to_float

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def _serialize_run(run: IngestionRun) -> dict[str, Any]:
    return {
        "run_id": str(run.run_id),
        "product_id": str(run.product_id) if run.product_id else None,
        "source": run.source,
        "function_name": run.function_name,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_s": _decimal_to_float(run.duration_s),
        "listings_fetched": run.listings_fetched,
        "listings_deduped": run.listings_deduped,
        "listings_persisted": run.listings_persisted,
        "listings_missing_price": run.listings_missing_price,
        "listings_rejected_title": run.listings_rejected_title,
        "filtering_stats": run.filtering_stats,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("/runs")
def list_ingestion_runs(
    source: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    product_id: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Paginated list of ingestion runs with filters."""
    query = db.query(IngestionRun)

    if source:
        query = query.filter(IngestionRun.source == source)
    if status_filter:
        query = query.filter(IngestionRun.status == status_filter)
    if product_id:
        query = query.filter(IngestionRun.product_id == product_id)
    if date_from:
        query = query.filter(IngestionRun.started_at >= date_from)
    if date_to:
        query = query.filter(IngestionRun.started_at <= date_to)

    total = query.count()
    runs = (
        query.order_by(desc(IngestionRun.started_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "runs": [_serialize_run(r) for r in runs],
    }


@router.get("/runs/{run_id}")
def get_ingestion_run(
    run_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Get a single ingestion run by ID."""
    run = db.query(IngestionRun).filter(IngestionRun.run_id == run_id).first()
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ingestion run not found")
    return _serialize_run(run)

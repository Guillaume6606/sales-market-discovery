"""Health and observability endpoints for monitoring ingestion pipeline."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session

from backend.routers.feedback import compute_precision_summary
from libs.common.db import get_db
from libs.common.models import (
    ConnectorAudit,
    IngestionRun,
    ListingDetailORM,
    ListingEnrichment,
    ListingObservation,
    ListingScore,
    ProductTemplate,
)
from libs.common.settings import settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/vision")
def get_vision_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    from libs.common.models import VisionBudget, VisionRequest

    warnings = []
    retired = {"gemini-2.0-flash", "gemini-2.0-flash-001", "gemini-2.0-flash-lite"}
    if settings.enrichment_llm_model in retired:
        warnings.append("retired_enrichment_model")
    if (
        settings.vision_enabled
        and settings.vision_provider == "scaleway"
        and not settings.scaleway_api_key
    ):
        warnings.append("scaleway_credentials_missing")
    if (
        settings.vision_enabled
        and settings.vision_provider == "gemini"
        and not (settings.gemini_api_key or settings.gcp_project_id)
    ):
        warnings.append("google_credentials_missing")
    period = datetime.now(UTC).strftime("%Y-%m")
    budgets = (
        db.query(VisionBudget).filter(VisionBudget.period == period).all()
        if settings.vision_enabled
        else []
    )
    counts = (
        db.query(VisionRequest.status, func.count(VisionRequest.request_key))
        .group_by(VisionRequest.status)
        .all()
        if settings.vision_enabled
        else []
    )
    return {
        "enabled": settings.vision_enabled,
        "mode": "shadow" if settings.vision_shadow_mode else "enforced",
        "provider": settings.vision_provider,
        "model": settings.vision_model,
        "enrichment_model": settings.enrichment_llm_model,
        "warnings": warnings,
        "requests": dict(counts),
        "period": period,
        "budgets": [
            {"currency": row.currency, "spent_or_reserved": float(row.reserved)} for row in budgets
        ],
        "limits": {
            "EUR": float(settings.vision_monthly_budget_eur),
            "USD": float(settings.vision_monthly_budget_usd),
        },
    }


FETCH_COMPLETED = ("success", "no_data", "error")
FETCH_SUCCESS = (IngestionRun.status == "success") & (
    (IngestionRun.listings_persisted > 0) | IngestionRun.listings_persisted.is_(None)
)
FETCH_NO_DATA = (IngestionRun.status == "no_data") | (
    (IngestionRun.status == "success") & (IngestionRun.listings_persisted == 0)
)


def _fetch_outcomes(db: Session, source: str, since: datetime) -> Any:
    return (
        db.query(
            func.count(IngestionRun.run_id).label("total"),
            func.count(case((FETCH_SUCCESS, IngestionRun.run_id), else_=None)).label("successes"),
            func.count(case((FETCH_NO_DATA, IngestionRun.run_id), else_=None)).label("no_data"),
            func.count(
                case((IngestionRun.status == "error", IngestionRun.run_id), else_=None)
            ).label("errors"),
        )
        .filter(
            IngestionRun.source == source,
            IngestionRun.started_at >= since,
            IngestionRun.status.in_(FETCH_COMPLETED),
        )
        .first()
    )


def summarize_outcomes(row: Any) -> dict[str, Any]:
    total = int(row.total or 0) if row else 0
    successes = int(row.successes or 0) if row else 0
    no_data = int(row.no_data or 0) if row else 0
    errors = int(row.errors or 0) if row else 0
    return {
        "completed_runs": total,
        "success_runs": successes,
        "no_data_runs": no_data,
        "error_runs": errors,
        "success_rate": round(successes / total, 2) if total else None,
        "no_data_rate": round(no_data / total, 2) if total else None,
        "error_rate": round(errors / total, 2) if total else None,
    }


def _ingestion_status(connectors: list[dict[str, Any]], stale_count: int) -> str:
    if stale_count or any(c["status"] in {"red", "yellow"} for c in connectors):
        return "yellow"
    if not connectors or any(c["status"] == "gray" for c in connectors):
        return "gray"
    return "green"


@router.get("/ingestion")
def get_ingestion_health(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Per-connector ingestion summary."""
    now = datetime.now(UTC)
    twenty_four_h_ago = now - timedelta(hours=24)
    seven_d_ago = now - timedelta(days=7)

    sources = db.query(IngestionRun.source).filter(IngestionRun.source.isnot(None)).distinct().all()

    result = []
    for (source,) in sources:
        # Last success/failure
        last_success = (
            db.query(IngestionRun)
            .filter(IngestionRun.source == source, FETCH_SUCCESS)
            .order_by(desc(IngestionRun.finished_at))
            .first()
        )
        last_failure = (
            db.query(IngestionRun)
            .filter(IngestionRun.source == source, IngestionRun.status == "error")
            .order_by(desc(IngestionRun.finished_at))
            .first()
        )

        # Success rate 24h
        outcomes_24h = summarize_outcomes(_fetch_outcomes(db, source, twenty_four_h_ago))

        # Success rate 7d
        outcomes_7d = summarize_outcomes(_fetch_outcomes(db, source, seven_d_ago))

        # Avg duration
        avg_duration = (
            db.query(func.avg(IngestionRun.duration_s))
            .filter(
                IngestionRun.source == source,
                FETCH_SUCCESS,
                IngestionRun.started_at >= seven_d_ago,
            )
            .scalar()
        )

        # Total listings persisted
        total_persisted = (
            db.query(func.sum(IngestionRun.listings_persisted))
            .filter(
                IngestionRun.source == source,
                FETCH_SUCCESS,
            )
            .scalar()
        ) or 0

        # 7d missing data aggregation (single query for both columns)
        missing_data = (
            db.query(
                func.sum(IngestionRun.listings_missing_price),
                func.sum(IngestionRun.listings_rejected_title),
            )
            .filter(
                IngestionRun.source == source,
                IngestionRun.started_at >= seven_d_ago,
            )
            .first()
        )
        missing_price_total = int(missing_data[0] or 0) if missing_data else 0
        rejected_title_total = int(missing_data[1] or 0) if missing_data else 0

        result.append(
            {
                "source": source,
                "last_success_at": last_success.finished_at.isoformat()
                if last_success and last_success.finished_at
                else None,
                "last_failure_at": last_failure.finished_at.isoformat()
                if last_failure and last_failure.finished_at
                else None,
                **{f"{key}_24h": value for key, value in outcomes_24h.items()},
                **{f"{key}_7d": value for key, value in outcomes_7d.items()},
                "avg_duration_s": round(float(avg_duration), 2) if avg_duration else None,
                "total_listings_persisted": int(total_persisted),
                "missing_price_total": int(missing_price_total),
                "rejected_title_total": int(rejected_title_total),
            }
        )

    return result


@router.get("/products")
def get_product_health(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Per-product staleness information."""
    now = datetime.now(UTC)

    products = db.query(ProductTemplate).filter(ProductTemplate.is_active.is_(True)).all()

    result = []
    for product in products:
        hours_since_ingestion = None
        is_stale = False

        if product.last_ingested_at:
            delta = (
                now - product.last_ingested_at.replace(tzinfo=UTC)
                if product.last_ingested_at.tzinfo is None
                else now - product.last_ingested_at
            )
            hours_since_ingestion = round(delta.total_seconds() / 3600, 1)
            is_stale = hours_since_ingestion > settings.stale_product_hours
        else:
            is_stale = True

        result.append(
            {
                "product_id": str(product.product_id),
                "name": product.name,
                "last_ingested_at": product.last_ingested_at.isoformat()
                if product.last_ingested_at
                else None,
                "hours_since_ingestion": hours_since_ingestion,
                "is_stale": is_stale,
            }
        )

    return result


@router.get("/overview")
def get_health_overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Dashboard summary with connector status colors and system status."""
    now = datetime.now(UTC)
    twenty_four_h_ago = now - timedelta(hours=24)

    # Connector status colors
    sources = db.query(IngestionRun.source).filter(IngestionRun.source.isnot(None)).distinct().all()

    connectors = []
    for (source,) in sources:
        outcomes = summarize_outcomes(_fetch_outcomes(db, source, twenty_four_h_ago))
        rate = (
            outcomes["success_runs"] / outcomes["completed_runs"]
            if outcomes["completed_runs"]
            else None
        )

        if rate is None:
            color = "gray"
        elif rate >= 0.8:
            color = "green"
        elif rate >= 0.5:
            color = "yellow"
        else:
            color = "red"

        connectors.append(
            {
                "source": source,
                "status": color,
                **{f"{key}_24h": value for key, value in outcomes.items()},
            }
        )

    # Stale product count
    products = db.query(ProductTemplate).filter(ProductTemplate.is_active.is_(True)).all()
    stale_count = 0
    for product in products:
        if not product.last_ingested_at:
            stale_count += 1
        else:
            ts = product.last_ingested_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if (now - ts).total_seconds() / 3600 > settings.stale_product_hours:
                stale_count += 1

    # Last 10 ingestion runs
    recent_runs = db.query(IngestionRun).order_by(desc(IngestionRun.started_at)).limit(10).all()
    recent_runs_data = [
        {
            "run_id": str(run.run_id),
            "source": run.source,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "listings_persisted": run.listings_persisted,
        }
        for run in recent_runs
    ]

    # System status
    system_status = _ingestion_status(connectors, stale_count)

    # Connector audit quality (last 7 days)
    audit_cutoff = datetime.now(UTC) - timedelta(days=7)
    audit_records = db.query(ConnectorAudit).filter(ConnectorAudit.audited_at >= audit_cutoff).all()

    connector_quality: dict[str, Any] = {}
    if audit_records:
        from ingestion.audit import compute_connector_accuracy

        connector_quality = compute_connector_accuracy(audit_records)

    return {
        "system_status": system_status,
        "ingestion_status": system_status,
        "connectors": connectors,
        "stale_product_count": stale_count,
        "recent_runs": recent_runs_data,
        "precision": compute_precision_summary(db),
        "connector_quality": connector_quality,
    }


@router.get("/enrichment")
def get_enrichment_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Check enrichment pipeline freshness."""
    total_active = (
        db.query(func.count(ListingObservation.obs_id))
        .filter(ListingObservation.is_stale == False)  # noqa: E712
        .scalar()
        or 0
    )

    detail_count = db.query(func.count(ListingDetailORM.detail_id)).scalar() or 0
    enrichment_count = db.query(func.count(ListingEnrichment.enrichment_id)).scalar() or 0
    score_count = db.query(func.count(ListingScore.score_id)).scalar() or 0

    latest_enrichment = db.query(func.max(ListingEnrichment.enriched_at)).scalar()
    latest_score = db.query(func.max(ListingScore.scored_at)).scalar()

    return {
        "detail_coverage": {
            "total_active_observations": total_active,
            "with_detail": detail_count,
            "coverage_pct": round(detail_count / total_active * 100, 1) if total_active else 0,
        },
        "enrichment_coverage": {
            "with_detail": detail_count,
            "with_enrichment": enrichment_count,
            "coverage_pct": round(enrichment_count / detail_count * 100, 1) if detail_count else 0,
        },
        "score_coverage": {
            "with_enrichment": enrichment_count,
            "with_score": score_count,
            "coverage_pct": round(score_count / enrichment_count * 100, 1)
            if enrichment_count
            else 0,
        },
        "latest_enrichment_at": latest_enrichment.isoformat() if latest_enrichment else None,
        "latest_score_at": latest_score.isoformat() if latest_score else None,
    }

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
            .filter(IngestionRun.source == source, IngestionRun.status == "success")
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
        runs_24h = (
            db.query(
                func.count(IngestionRun.run_id).label("total"),
                func.count(
                    case(
                        (IngestionRun.status == "success", IngestionRun.run_id),
                        else_=None,
                    )
                ).label("successes"),
            )
            .filter(
                IngestionRun.source == source,
                IngestionRun.started_at >= twenty_four_h_ago,
            )
            .first()
        )

        # Success rate 7d
        runs_7d = (
            db.query(
                func.count(IngestionRun.run_id).label("total"),
                func.count(
                    case(
                        (IngestionRun.status == "success", IngestionRun.run_id),
                        else_=None,
                    )
                ).label("successes"),
            )
            .filter(
                IngestionRun.source == source,
                IngestionRun.started_at >= seven_d_ago,
            )
            .first()
        )

        # Avg duration
        avg_duration = (
            db.query(func.avg(IngestionRun.duration_s))
            .filter(
                IngestionRun.source == source,
                IngestionRun.status == "success",
                IngestionRun.started_at >= seven_d_ago,
            )
            .scalar()
        )

        # Total listings persisted
        total_persisted = (
            db.query(func.sum(IngestionRun.listings_persisted))
            .filter(
                IngestionRun.source == source,
                IngestionRun.status == "success",
            )
            .scalar()
        ) or 0

        total_24h = runs_24h.total if runs_24h else 0
        successes_24h = runs_24h.successes if runs_24h else 0
        total_7d = runs_7d.total if runs_7d else 0
        successes_7d = runs_7d.successes if runs_7d else 0

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
                "success_rate_24h": round(successes_24h / total_24h, 2) if total_24h > 0 else None,
                "success_rate_7d": round(successes_7d / total_7d, 2) if total_7d > 0 else None,
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
        runs_24h = (
            db.query(
                func.count(IngestionRun.run_id).label("total"),
                func.count(
                    case(
                        (IngestionRun.status == "success", IngestionRun.run_id),
                        else_=None,
                    )
                ).label("successes"),
            )
            .filter(
                IngestionRun.source == source,
                IngestionRun.started_at >= twenty_four_h_ago,
            )
            .first()
        )

        total = runs_24h.total if runs_24h else 0
        successes = runs_24h.successes if runs_24h else 0
        rate = successes / total if total > 0 else 0.0

        if rate >= 0.8:
            color = "green"
        elif rate >= 0.5:
            color = "yellow"
        else:
            color = "red"

        connectors.append({"source": source, "status": color, "success_rate_24h": round(rate, 2)})

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
    any_red = any(c["status"] == "red" for c in connectors)
    system_status = "red" if (any_red or stale_count > 0) else "green"

    # Connector audit quality (last 7 days)
    audit_cutoff = datetime.now(UTC) - timedelta(days=7)
    audit_records = db.query(ConnectorAudit).filter(ConnectorAudit.audited_at >= audit_cutoff).all()

    connector_quality: dict[str, Any] = {}
    if audit_records:
        from ingestion.audit import compute_connector_accuracy

        connector_quality = compute_connector_accuracy(audit_records)

    return {
        "system_status": system_status,
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

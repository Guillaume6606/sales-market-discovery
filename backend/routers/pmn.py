"""PMN accuracy measurement endpoints."""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from libs.common.db import get_db
from libs.common.models import (
    ListingObservation,
    MarketPriceNormal,
    PMNHistory,
    ProductTemplate,
)
from libs.common.utils import decimal_to_float as _decimal_to_float

router = APIRouter(tags=["pmn"])


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware (UTC)."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _compute_accuracy_for_snapshots(
    snapshots: list[PMNHistory],
    sold_obs: list[ListingObservation],
) -> dict[str, Any]:
    """Compute accuracy metrics for PMN snapshots against sold observations."""
    if not snapshots or not sold_obs:
        return {
            "snapshots": [],
            "overall_mae": None,
            "overall_mean_pct_error": None,
            "overall_hit_rate": None,
            "matched_count": 0,
        }

    now = datetime.now(UTC)
    snapshot_results = []
    all_errors = []
    all_pct_errors = []
    all_hits = 0
    all_matched = 0

    for i, snap in enumerate(snapshots):
        window_start = _ensure_aware(snap.computed_at)

        if i + 1 < len(snapshots):
            window_end = _ensure_aware(snapshots[i + 1].computed_at)
        else:
            window_end = now

        pmn = _decimal_to_float(snap.pmn)
        pmn_low = _decimal_to_float(snap.pmn_low)
        pmn_high = _decimal_to_float(snap.pmn_high)
        if pmn is None:
            continue

        # Filter sold observations in this window
        matched = []
        for obs in sold_obs:
            obs_at = _ensure_aware(obs.observed_at)
            if obs_at and window_start <= obs_at < window_end and obs.price is not None:
                matched.append(obs)

        if not matched:
            snapshot_results.append(
                {
                    "computed_at": snap.computed_at.isoformat() if snap.computed_at else None,
                    "pmn": pmn,
                    "matched_count": 0,
                    "mae": None,
                    "mean_pct_error": None,
                    "hit_rate": None,
                }
            )
            continue

        errors = [abs(float(obs.price) - pmn) for obs in matched]
        pct_errors = [abs(float(obs.price) - pmn) / pmn * 100 for obs in matched]
        hits = sum(
            1
            for obs in matched
            if pmn_low is not None
            and pmn_high is not None
            and pmn_low <= float(obs.price) <= pmn_high
        )

        mae = sum(errors) / len(errors)
        mean_pct = sum(pct_errors) / len(pct_errors)
        hit_rate = hits / len(matched)

        all_errors.extend(errors)
        all_pct_errors.extend(pct_errors)
        all_hits += hits
        all_matched += len(matched)

        snapshot_results.append(
            {
                "computed_at": snap.computed_at.isoformat() if snap.computed_at else None,
                "pmn": pmn,
                "matched_count": len(matched),
                "mae": round(mae, 2),
                "mean_pct_error": round(mean_pct, 2),
                "hit_rate": round(hit_rate, 4),
            }
        )

    overall_mae = round(sum(all_errors) / len(all_errors), 2) if all_errors else None
    overall_pct = round(sum(all_pct_errors) / len(all_pct_errors), 2) if all_pct_errors else None
    overall_hit = round(all_hits / all_matched, 4) if all_matched > 0 else None

    return {
        "snapshots": snapshot_results,
        "overall_mae": overall_mae,
        "overall_mean_pct_error": overall_pct,
        "overall_hit_rate": overall_hit,
        "matched_count": all_matched,
    }


@router.get("/products/{product_id}/pmn-accuracy")
def get_product_pmn_accuracy(
    product_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Per-product PMN accuracy against sold listings."""
    product = db.query(ProductTemplate).filter(ProductTemplate.product_id == product_id).first()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    snapshots = (
        db.query(PMNHistory)
        .filter(PMNHistory.product_id == product_id)
        .order_by(PMNHistory.computed_at)
        .all()
    )

    if not snapshots:
        return {
            "product_id": product_id,
            "overall_mae": None,
            "overall_mean_pct_error": None,
            "overall_hit_rate": None,
            "matched_count": 0,
            "snapshots": [],
        }

    sold_obs = (
        db.query(ListingObservation)
        .filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold.is_(True),
            ListingObservation.price.isnot(None),
        )
        .all()
    )

    accuracy = _compute_accuracy_for_snapshots(snapshots, sold_obs)
    return {"product_id": product_id, **accuracy}


@router.get("/analytics/pmn-accuracy")
def get_aggregate_pmn_accuracy(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Aggregate PMN accuracy across all products."""
    product_ids = db.query(PMNHistory.product_id).distinct().all()

    if not product_ids:
        return {
            "overall_mae": None,
            "overall_mean_pct_error": None,
            "overall_hit_rate": None,
            "product_count": 0,
            "worst_products": [],
            "best_products": [],
            "low_confidence_products": [],
        }

    pid_list = [pid for (pid,) in product_ids]

    # Batch-load all data in 3 queries instead of 3N+1
    all_snapshots = (
        db.query(PMNHistory)
        .filter(PMNHistory.product_id.in_(pid_list))
        .order_by(PMNHistory.product_id, PMNHistory.computed_at)
        .all()
    )
    all_sold = (
        db.query(ListingObservation)
        .filter(
            ListingObservation.product_id.in_(pid_list),
            ListingObservation.is_sold.is_(True),
            ListingObservation.price.isnot(None),
        )
        .all()
    )
    all_products = db.query(ProductTemplate).filter(ProductTemplate.product_id.in_(pid_list)).all()

    # Group by product_id
    snapshots_by_pid: dict[str, list] = defaultdict(list)
    for snap in all_snapshots:
        snapshots_by_pid[str(snap.product_id)].append(snap)

    sold_by_pid: dict[str, list] = defaultdict(list)
    for obs in all_sold:
        sold_by_pid[str(obs.product_id)].append(obs)

    product_names = {str(p.product_id): p.name for p in all_products}

    product_results = []
    total_weighted_mae = 0.0
    total_weighted_pct = 0.0
    total_weighted_hits = 0.0
    total_matched = 0

    for pid_str in [str(p) for p in pid_list]:
        accuracy = _compute_accuracy_for_snapshots(
            snapshots_by_pid.get(pid_str, []),
            sold_by_pid.get(pid_str, []),
        )

        entry = {
            "product_id": pid_str,
            "name": product_names.get(pid_str),
            "mae": accuracy["overall_mae"],
            "mean_pct_error": accuracy["overall_mean_pct_error"],
            "hit_rate": accuracy["overall_hit_rate"],
            "matched_count": accuracy["matched_count"],
        }
        product_results.append(entry)

        if accuracy["matched_count"] > 0:
            total_weighted_mae += (accuracy["overall_mae"] or 0) * accuracy["matched_count"]
            total_weighted_pct += (accuracy["overall_mean_pct_error"] or 0) * accuracy[
                "matched_count"
            ]
            total_weighted_hits += (accuracy["overall_hit_rate"] or 0) * accuracy["matched_count"]
            total_matched += accuracy["matched_count"]

    overall_mae = round(total_weighted_mae / total_matched, 2) if total_matched else None
    overall_pct = round(total_weighted_pct / total_matched, 2) if total_matched else None
    overall_hit = round(total_weighted_hits / total_matched, 4) if total_matched else None

    # Sort for worst/best
    with_mae = [p for p in product_results if p["mae"] is not None]
    worst = sorted(with_mae, key=lambda x: x["mae"], reverse=True)[:5]

    with_hit = [p for p in product_results if p["hit_rate"] is not None]
    best = sorted(with_hit, key=lambda x: x["hit_rate"], reverse=True)[:5]

    # Low confidence products
    low_conf_rows = db.query(MarketPriceNormal).filter(MarketPriceNormal.confidence < 0.3).all()
    low_confidence = [
        {
            "product_id": str(r.product_id),
            "confidence": _decimal_to_float(r.confidence),
            "pmn": _decimal_to_float(r.pmn),
        }
        for r in low_conf_rows
    ]

    return {
        "overall_mae": overall_mae,
        "overall_mean_pct_error": overall_pct,
        "overall_hit_rate": overall_hit,
        "product_count": len(product_ids),
        "total_matched_sales": total_matched,
        "worst_products": worst,
        "best_products": best,
        "low_confidence_products": low_confidence,
    }

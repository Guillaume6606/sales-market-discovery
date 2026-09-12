"""Apply cached factual vision results without replacing financial or delivery evidence."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from libs.common.db import SessionLocal
from libs.common.models import ListingDetailORM, ListingObservation, ProductTemplate
from libs.common.settings import settings


def _description(obs: ListingObservation, detail: ListingDetailORM | None) -> str:
    return (
        f"{detail.description or ''}\nSeller condition: {obs.condition or 'unknown'}"
        if detail
        else ""
    )


def _target(product: ProductTemplate) -> str:
    return f"{product.name}\n{product.search_query}"


def input_fingerprint(
    obs: ListingObservation, detail: ListingDetailORM | None, product: ProductTemplate
) -> str:
    from libs.common.vision_schema import SCHEMA_VERSION
    from libs.common.vision_service import PROMPT_VERSION

    payload = [
        obs.title,
        _description(obs, detail),
        detail.photo_urls if detail else [],
        _target(product),
        settings.vision_provider,
        settings.vision_model,
        settings.vision_local_model_revision,
        PROMPT_VERSION,
        SCHEMA_VERSION,
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _current_result(
    obs: ListingObservation, detail: ListingDetailORM | None, product: ProductTemplate
) -> bool:
    result = obs.vision_result or {}
    if result.get("pipeline") != "vision" or result.get("input_fingerprint") != input_fingerprint(
        obs, detail, product
    ):
        return False
    try:
        checked = datetime.fromisoformat(result["checked_at"])
        return timedelta(0) <= datetime.now(UTC) - checked <= timedelta(hours=24)
    except (ValueError, KeyError, TypeError):
        return False


def vision_review_reasons(
    obs: ListingObservation, detail: ListingDetailORM | None, product: ProductTemplate
) -> list[str]:
    if not settings.vision_enabled or settings.vision_shadow_mode:
        return []
    result = obs.vision_result or {}
    if result.get("pipeline") != "vision":
        return ["vision_pending"]
    if not _current_result(obs, detail, product):
        return ["vision_stale"]
    if result.get("status") != "completed":
        return [f"vision_{result.get('status', 'pending')}"]
    extraction = result.get("extraction") or {}
    reasons = []
    item_class = extraction.get("item_class", "uncertain")
    if item_class != "exact_device":
        reasons.append(
            "vision_bundle_review_required"
            if item_class == "device_bundle"
            else f"vision_{item_class}"
        )
    if not extraction.get("model"):
        reasons.append("vision_model_unknown")
    if item_class == "exact_device" and not str(extraction.get("variant") or "").strip():
        reasons.append("vision_variant_unknown")
    identity_unknown = {"model", "variant", "generation", "capacity", "storage", "mount"}
    if any(
        any(word in field.casefold() for word in identity_unknown)
        for field in extraction.get("unknown_fields", [])
    ):
        reasons.append("vision_identity_uncertain")
    if extraction.get("contradictions"):
        reasons.append("vision_contradictions")
    if extraction.get("visible_defects"):
        reasons.append("vision_visible_defects_review_required")
    return reasons


async def run_listing_vision_batch(product_id: str | None = None) -> dict[str, Any]:
    from ingestion.enrichment import _persist_enrichment
    from libs.common.vision_service import extract_listing

    if not settings.vision_enabled:
        return {"status": "disabled"}
    counts: dict[str, Any] = {"status": "success", "analysed": 0, "cached": 0}
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.alert_freshness_minutes)
    with SessionLocal() as db:
        query = (
            db.query(ListingObservation, ListingDetailORM, ProductTemplate)
            .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
            .outerjoin(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
            .filter(
                ListingObservation.is_sold.is_(False),
                ListingObservation.is_stale.is_(False),
                ListingObservation.last_seen_at >= cutoff,
                ProductTemplate.is_active.is_(True),
            )
        )
        if product_id is not None:
            query = query.filter(ListingObservation.product_id == product_id)
        rows = (
            query.order_by(
                ListingObservation.vision_checked_at.asc().nullsfirst(), ListingObservation.obs_id
            )
            .limit(settings.vision_batch_size)
            .all()
        )
        for obs, detail, product in rows:
            if _current_result(obs, detail, product) and (obs.vision_result or {}).get(
                "status"
            ) in {"completed", "text_only"}:
                continue
            if detail is None:
                result = {"status": "pending", "error": "detail_unavailable"}
            else:
                response = await extract_listing(
                    obs.title or "",
                    _description(obs, detail),
                    detail.photo_urls or [],
                    target=_target(product),
                )
                result = response.model_dump(mode="json")
            result.update(
                pipeline="vision",
                input_fingerprint=input_fingerprint(obs, detail, product),
                checked_at=datetime.now(UTC).isoformat(),
            )
            obs.vision_result = result
            obs.vision_checked_at = datetime.now(UTC)
            db.commit()
            counts["analysed"] += 1
            counts[result["status"]] = counts.get(result["status"], 0) + 1
            counts["cached"] += bool(result.get("cache_hit"))
            extraction = result.get("extraction")
            if extraction and not settings.vision_shadow_mode:
                _persist_enrichment(
                    db,
                    obs.obs_id,
                    {
                        "accessories_included": extraction.get("included_accessories", []),
                        "_model": result.get("model"),
                        "_raw_response": result,
                    },
                )
        if counts.get("error") or counts.get("budget_exhausted") or counts.get("pending"):
            counts["status"] = "partial"
    return counts

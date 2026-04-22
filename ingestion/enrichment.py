"""Hourly LLM enrichment batch job.

Queries listings that have ``listing_detail`` but no ``listing_enrichment``
(or stale enrichment older than ``enrichment_re_enrichment_age_days`` days),
calls Gemini Flash LLM to analyse each listing, and persists results to the
``listing_enrichment`` table via an upsert.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ingestion.enrichment_prompt import build_enrichment_prompt, parse_enrichment_response
from libs.common.db import SessionLocal
from libs.common.llm_service import get_genai_client
from libs.common.models import (
    ListingDetailORM,
    ListingEnrichment,
    ListingObservation,
    MarketPriceNormal,
    ProductTemplate,
)
from libs.common.settings import settings

logger = logging.getLogger(__name__)


def _get_unenriched_listings(db: Session, limit: int) -> list[tuple]:
    """Return listings that need enrichment, ordered by ascending price.

    The result is split into two buckets:

    * **Fresh** — listings that have a ``listing_detail`` row but *no*
      ``listing_enrichment`` row and are not stale.  These are returned first,
      up to *limit*.
    * **Re-enrichment** — listings whose existing enrichment is older than
      ``enrichment_re_enrichment_age_days`` days.  At most
      ``enrichment_re_enrichment_batch_size`` re-enrichment candidates are
      appended to fill any remaining capacity.

    Args:
        db: Active SQLAlchemy session.
        limit: Maximum total number of rows to return.

    Returns:
        List of ``(ListingObservation, ListingDetailORM, MarketPriceNormal | None,
        ProductTemplate)`` tuples.
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.enrichment_re_enrichment_age_days)

    fresh: list[tuple] = (
        db.query(ListingObservation, ListingDetailORM, MarketPriceNormal, ProductTemplate)
        .join(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
        .outerjoin(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
        .outerjoin(MarketPriceNormal, MarketPriceNormal.product_id == ListingObservation.product_id)
        .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
        .filter(
            ListingEnrichment.obs_id.is_(None),
            ListingObservation.is_stale == False,  # noqa: E712
        )
        .order_by(ListingObservation.price.asc())
        .limit(limit)
        .all()
    )

    re_limit = min(
        settings.enrichment_re_enrichment_batch_size,
        max(0, limit - len(fresh)),
    )
    stale: list[tuple] = []
    if re_limit > 0:
        stale = (
            db.query(ListingObservation, ListingDetailORM, MarketPriceNormal, ProductTemplate)
            .join(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
            .join(ListingEnrichment, ListingEnrichment.obs_id == ListingObservation.obs_id)
            .outerjoin(
                MarketPriceNormal,
                MarketPriceNormal.product_id == ListingObservation.product_id,
            )
            .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
            .filter(
                ListingEnrichment.enriched_at < cutoff,
                ListingObservation.is_stale == False,  # noqa: E712
            )
            .order_by(ListingEnrichment.enriched_at.asc())
            .limit(re_limit)
            .all()
        )

    return fresh + stale


def _enrich_single_listing(
    obs: ListingObservation,
    detail: ListingDetailORM,
    pmn_row: MarketPriceNormal | None,
    product: ProductTemplate,
    client: Any,
) -> dict[str, Any] | None:
    """Call the LLM for a single listing and return the parsed enrichment dict.

    Args:
        obs: The ``ListingObservation`` ORM row.
        detail: The associated ``ListingDetailORM`` row.
        pmn_row: The ``MarketPriceNormal`` row for the product, or ``None``.
        product: The ``ProductTemplate`` row.
        client: Initialised ``google.genai.Client`` instance.

    Returns:
        A validated enrichment dict (keys defined by ``ALL_REQUIRED_KEYS``) with
        two additional private keys ``_raw_response`` and ``_tokens``, or
        ``None`` on LLM / parse failure.
    """
    pmn_value = float(pmn_row.pmn) if pmn_row and pmn_row.pmn else None
    days_since: int | None = None
    if detail.original_posted_at:
        days_since = (datetime.now(UTC) - detail.original_posted_at).days

    prompt = build_enrichment_prompt(
        title=obs.title or "",
        description=detail.description,
        condition_raw=obs.condition,
        price=float(obs.price) if obs.price else 0.0,
        currency=obs.currency or "EUR",
        category=product.category.name if product.category else None,
        brand=product.brand,
        pmn=pmn_value,
        photo_urls=detail.photo_urls or [],
        days_since_posted=days_since,
    )

    try:
        response = client.models.generate_content(
            model=settings.enrichment_llm_model,
            contents=[prompt],
            config={"temperature": 0.1, "response_mime_type": "application/json"},
        )
        raw_text: str = response.text.strip()
        tokens: int | None = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            tokens = getattr(response.usage_metadata, "total_token_count", None)
    except Exception:
        logger.exception("LLM call failed for obs_id=%s", obs.obs_id)
        return None

    result = parse_enrichment_response(raw_text)
    if result is not None:
        result["_raw_response"] = {"raw_text": raw_text, "parsed": result.copy()}
        result["_tokens"] = tokens
    return result


def _persist_enrichment(db: Session, obs_id: int, result: dict[str, Any]) -> bool:
    """Upsert a single enrichment row into ``listing_enrichment``.

    Args:
        db: Active SQLAlchemy session.
        obs_id: Primary key of the linked ``listing_observation`` row.
        result: Validated enrichment dict as returned by ``_enrich_single_listing``.

    Returns:
        ``True`` on successful commit, ``False`` on error.
    """
    values: dict[str, Any] = {
        "obs_id": obs_id,
        "urgency_score": result.get("urgency_score"),
        "urgency_keywords": result.get("urgency_keywords"),
        "has_original_box": result.get("has_original_box"),
        "has_receipt_or_invoice": result.get("has_receipt_or_invoice"),
        "accessories_included": result.get("accessories_included"),
        "accessories_completeness": result.get("accessories_completeness"),
        "photo_quality_score": result.get("photo_quality_score"),
        "listing_quality_score": result.get("listing_quality_score"),
        "condition_confidence": result.get("condition_confidence"),
        "fakeness_probability": result.get("fakeness_probability"),
        "seller_motivation_score": result.get("seller_motivation_score"),
        "llm_model": settings.enrichment_llm_model,
        "llm_raw_response": result.get("_raw_response"),
        "enriched_at": datetime.now(UTC),
        "cost_tokens": result.get("_tokens"),
    }
    stmt = insert(ListingEnrichment).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["obs_id"],
        set_={k: v for k, v in values.items() if k != "obs_id"},
    )
    try:
        db.execute(stmt)
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("Failed to persist enrichment for obs_id=%s", obs_id)
        return False


async def run_enrichment_batch(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """ARQ-compatible entrypoint for the hourly enrichment batch job.

    Skips immediately when ``settings.enrichment_enabled`` is ``False`` or when
    no Gemini client can be created (missing API key / GCP credentials).

    Args:
        ctx: ARQ worker context (unused but required by the ARQ function
            signature convention).

    Returns:
        A result dict with keys ``status``, and on success: ``enriched``,
        ``failed``, ``total_tokens``.
    """
    if not settings.enrichment_enabled:
        logger.info("Enrichment disabled via settings — skipping batch")
        return {"status": "disabled"}

    client = get_genai_client()
    if not client:
        logger.warning("No Gemini client available — skipping enrichment batch")
        return {"status": "no_client"}

    db = SessionLocal()
    try:
        candidates = _get_unenriched_listings(db, settings.enrichment_batch_size)
        logger.info("Enrichment batch: %d candidates to process", len(candidates))

        enriched = 0
        failed = 0
        total_tokens = 0

        for obs, detail, pmn_row, product in candidates:
            result = _enrich_single_listing(obs, detail, pmn_row, product, client)
            if result is not None and _persist_enrichment(db, obs.obs_id, result):
                enriched += 1
                total_tokens += result.get("_tokens") or 0
            else:
                failed += 1

        logger.info(
            "Enrichment batch complete: %d enriched, %d failed, %d tokens used",
            enriched,
            failed,
            total_tokens,
        )
        return {
            "status": "success",
            "enriched": enriched,
            "failed": failed,
            "total_tokens": total_tokens,
        }
    finally:
        db.close()

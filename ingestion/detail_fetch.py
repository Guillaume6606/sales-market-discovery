"""Detail fetch orchestration: candidate selection and persistence."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from libs.common.models import ListingDetail, ListingDetailORM, ListingObservation
from libs.common.settings import settings

logger = logging.getLogger(__name__)

RATE_LIMITS: dict[str, float] = {
    "ebay": settings.detail_fetch_rate_limit_ebay,
    "leboncoin": settings.detail_fetch_rate_limit_lbc,
    "vinted": settings.detail_fetch_rate_limit_vinted,
}


def should_fetch_detail(
    price: Decimal | None,
    pmn: Decimal | None,
    pmn_threshold: float,
    price_min: Decimal | None,
    price_max: Decimal | None,
) -> bool:
    """Determine whether a listing observation qualifies for detail fetching.

    Args:
        price: The listing price. Returns False immediately if None.
        pmn: The Price of Market Normal for this product. When available, used
            as the primary filter criterion.
        pmn_threshold: Multiplier applied to PMN. Listings at or below
            ``pmn * pmn_threshold`` are candidates (e.g. 1.1 means up to 10%
            above PMN).
        price_min: Lower bound of the product price range (from ProductTemplate).
        price_max: Upper bound of the product price range (from ProductTemplate).

    Returns:
        True if the listing should have its detail page fetched, False otherwise.
    """
    if price is None:
        return False

    if pmn is not None:
        return float(price) <= float(pmn) * pmn_threshold

    # No PMN yet — use price range as a fallback filter if configured.
    if price_min is not None or price_max is not None:
        return True

    # Full cold start: no PMN and no price range — fetch everything.
    return True


def persist_listing_detail(db: Session, detail: ListingDetail) -> bool:
    """Upsert a ListingDetail record into the database.

    Args:
        db: An active SQLAlchemy session.
        detail: The detail payload returned by a connector's fetch_detail call.

    Returns:
        True on success, False if the upsert failed (session is rolled back).
    """
    photo_urls = detail.photo_urls or []
    values: dict = {
        "obs_id": detail.obs_id,
        "description": detail.description,
        "description_length": len(detail.description) if detail.description else None,
        "photo_urls": photo_urls,
        "photo_count": len(photo_urls),
        "local_pickup_only": detail.local_pickup_only,
        "negotiation_enabled": detail.negotiation_enabled,
        "original_posted_at": detail.original_posted_at,
        "seller_account_age_days": detail.seller_account_age_days,
        "seller_transaction_count": detail.seller_transaction_count,
        "view_count": detail.view_count,
        "favorite_count": detail.favorite_count,
        "fetched_at": datetime.now(UTC),
    }
    stmt = insert(ListingDetailORM).values(**values)
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
        logger.exception("Failed to persist detail for obs_id=%s", detail.obs_id)
        return False


async def fetch_and_persist_details(
    db: Session,
    observations: list[ListingObservation],
    source: str,
    pmn: Decimal | None,
    price_min: Decimal | None,
    price_max: Decimal | None,
    fetch_detail_fn,  # type: ignore[type-arg]
) -> int:
    """Fetch and persist detail pages for candidate observations.

    Iterates over ``observations``, applies :func:`should_fetch_detail` to
    select candidates, sleeps between requests to respect rate limits, calls
    ``fetch_detail_fn``, and persists the result.

    Args:
        db: An active SQLAlchemy session.
        observations: Listing observations produced during the ingestion run.
        source: Marketplace identifier (``"ebay"``, ``"leboncoin"``,
            ``"vinted"``). Used to select the configured rate limit.
        pmn: Current Price of Market Normal for the product, or None when no
            PMN has been computed yet.
        price_min: Lower bound from the product template (may be None).
        price_max: Upper bound from the product template (may be None).
        fetch_detail_fn: Callable ``(listing_id, *, obs_id) -> ListingDetail``
            — may be a regular function or a coroutine function.

    Returns:
        Number of detail records successfully persisted.
    """
    if not settings.detail_fetch_enabled:
        return 0

    threshold = settings.detail_fetch_pmn_threshold
    rate_limit = RATE_LIMITS.get(source, 1.0)
    persisted = 0

    for obs in observations:
        if not should_fetch_detail(obs.price, pmn, threshold, price_min, price_max):
            continue

        await asyncio.sleep(rate_limit)

        try:
            if asyncio.iscoroutinefunction(fetch_detail_fn):
                detail = await fetch_detail_fn(obs.listing_id, obs_id=obs.obs_id)
            else:
                detail = fetch_detail_fn(obs.listing_id, obs_id=obs.obs_id)
        except Exception:
            logger.exception("Detail fetch failed for %s/%s", source, obs.listing_id)
            continue

        if detail and persist_listing_detail(db, detail):
            persisted += 1

    logger.info(
        "Detail fetch for %s: %d/%d persisted",
        source,
        persisted,
        len(observations),
    )
    return persisted

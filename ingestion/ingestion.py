from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from loguru import logger
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, make_transient

from ingestion.connectors.ebay import fetch_ebay_listings
from ingestion.connectors.leboncoin_api import (
    fetch_leboncoin_api_listings,
)
from ingestion.connectors.vinted import fetch_vinted_listings
from ingestion.constants import SUPPORTED_PROVIDERS
from ingestion.filtering import filter_listings_multi_stage
from ingestion.pricing import pmn_from_prices
from ingestion.run_tracker import filtering_stats_to_dict, track_ingestion_run
from ingestion.schemas import ProductTemplateSnapshot
from ingestion.validation import validate_listings
from libs.common.db import SessionLocal
from libs.common.models import (
    IngestionRun,
    Listing,
    ListingObservation,
    ListingObservationEvent,
    ProductDailyMetrics,
    ProductTemplate,
)
from libs.common.settings import settings
from libs.common.utils import decimal_to_float as _decimal_to_float


def _snapshot_product(product: ProductTemplate) -> ProductTemplateSnapshot:
    category_name = product.category.name if product.category else None
    providers = product.providers or []
    words_to_avoid = product.words_to_avoid or []
    return ProductTemplateSnapshot(
        product_id=str(product.product_id),
        name=product.name,
        description=product.description,
        search_query=product.search_query,
        category_id=str(product.category_id),
        category_name=category_name,
        brand=product.brand,
        price_min=_decimal_to_float(product.price_min),
        price_max=_decimal_to_float(product.price_max),
        providers=list(providers),
        words_to_avoid=list(words_to_avoid),
        enable_llm_validation=product.enable_llm_validation,
        is_active=product.is_active,
    )


def _load_product_snapshot(product_id: str) -> ProductTemplateSnapshot | None:
    with SessionLocal() as db:
        product = (
            db.query(ProductTemplate)
            .options(joinedload(ProductTemplate.category))
            .filter(ProductTemplate.product_id == product_id)
            .first()
        )
        if not product or not product.is_active:
            return None
        return _snapshot_product(product)


def _compose_search_term(snapshot: ProductTemplateSnapshot) -> str:
    if snapshot.brand:
        if snapshot.brand.lower() not in snapshot.search_query.lower():
            return f"{snapshot.search_query} {snapshot.brand}".strip()
    return snapshot.search_query


def _dedupe_listings(listings: Iterable[Listing]) -> list[Listing]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Listing] = []
    for listing in listings:
        key = (listing.source, listing.listing_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(listing)
    return deduped


def _upsert_listing(
    db: Session,
    product: ProductTemplate,
    listing: Listing,
    *,
    force_is_sold: bool | None = None,
    llm_validation_result: dict | None = None,
    screenshot_path: str | None = None,
) -> bool:
    if (force_is_sold or listing.is_sold) and listing.evidence_type != "verified_sale":
        logger.warning("Rejecting unverified sold observation: {}", listing.listing_id)
        return False
    listing_source = listing.source
    now_utc = datetime.now(UTC)

    savepoint = db.begin_nested()
    try:
        existing = (
            db.query(ListingObservation)
            .filter(
                and_(
                    ListingObservation.listing_id == listing.listing_id,
                    ListingObservation.source == listing_source,
                    ListingObservation.product_id == product.product_id,
                )
            )
            .first()
        )

        observed_at = listing.observed_at
        if observed_at and observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)

        is_sold = force_is_sold if force_is_sold is not None else listing.is_sold

        price = Decimal(str(listing.price)) if listing.price is not None else None
        shipping = (
            Decimal(str(listing.shipping_cost)) if listing.shipping_cost is not None else None
        )
        if existing:
            changed = (
                existing.price != price
                or existing.is_sold != is_sold
                or existing.title != listing.title
                or existing.condition != listing.condition_raw
                or existing.shipping_cost != shipping
                or existing.currency != listing.currency
                or existing.evidence_type != listing.evidence_type
                or existing.url != listing.url
                or existing.delivery_to_france != listing.delivery_to_france
                or existing.delivery_evidence != listing.delivery_evidence
            )
            existing.price = listing.price
            existing.title = listing.title
            existing.currency = listing.currency
            existing.condition = listing.condition_raw
            existing.is_sold = is_sold
            existing.seller_rating = listing.seller_rating
            existing.shipping_cost = listing.shipping_cost
            existing.delivery_to_france = listing.delivery_to_france
            existing.delivery_evidence = listing.delivery_evidence
            existing.location = listing.location
            existing.first_seen_at = existing.first_seen_at or existing.observed_at or now_utc
            existing.evidence_type = listing.evidence_type
            if changed:
                from libs.common.valuation_models import ValuationListingReview

                db.query(ValuationListingReview).filter(
                    ValuationListingReview.obs_id == existing.obs_id,
                    ValuationListingReview.is_active.is_(True),
                ).update({"is_active": False}, synchronize_session="fetch")
                existing.updated_at = now_utc
                existing.vision_result = None
                existing.vision_checked_at = None
                if (existing.llm_validation_result or {}).get("pipeline") == "vision":
                    existing.llm_validated = False
                    existing.llm_validation_result = {"pipeline": "vision", "status": "pending"}
                    existing.llm_validated_at = None
            existing.url = listing.url
            existing.last_seen_at = now_utc
            existing.is_stale = False

            # Update LLM validation fields if provided
            if llm_validation_result is not None:
                pending_vision = (
                    llm_validation_result.get("pipeline") == "vision"
                    and llm_validation_result.get("status") == "pending"
                )
                if not (
                    pending_vision
                    and not changed
                    and (existing.llm_validation_result or {}).get("pipeline") == "vision"
                ):
                    existing.llm_validated = not pending_vision
                    existing.llm_validation_result = llm_validation_result
                    existing.llm_validated_at = None if pending_vision else now_utc
            if screenshot_path:
                existing.screenshot_path = screenshot_path
        else:
            observation = ListingObservation(
                product_id=product.product_id,
                source=listing_source,
                listing_id=listing.listing_id,
                title=listing.title,
                price=listing.price,
                currency=listing.currency,
                condition=listing.condition_raw,
                is_sold=is_sold,
                seller_rating=listing.seller_rating,
                shipping_cost=listing.shipping_cost,
                delivery_to_france=listing.delivery_to_france,
                delivery_evidence=listing.delivery_evidence,
                location=listing.location,
                observed_at=observed_at,
                url=listing.url,
                first_seen_at=now_utc,
                updated_at=now_utc,
                evidence_type=listing.evidence_type,
                last_seen_at=now_utc,
                llm_validated=llm_validation_result is not None
                and llm_validation_result.get("pipeline") != "vision",
                llm_validation_result=llm_validation_result,
                llm_validated_at=now_utc
                if llm_validation_result and llm_validation_result.get("pipeline") != "vision"
                else None,
                screenshot_path=screenshot_path,
            )
            db.add(observation)
            db.flush()
            existing = observation
            changed = True

        if changed:
            db.add(
                ListingObservationEvent(
                    obs_id=existing.obs_id,
                    recorded_at=now_utc,
                    price=listing.price,
                    currency=listing.currency,
                    is_sold=is_sold,
                    evidence_type=listing.evidence_type,
                    payload={
                        "title": listing.title,
                        "condition": listing.condition_raw,
                        "shipping_cost": listing.shipping_cost,
                        "delivery_to_france": listing.delivery_to_france,
                        "delivery_evidence": listing.delivery_evidence,
                        "url": listing.url,
                    },
                )
            )
        savepoint.commit()
        return True
    except IntegrityError:
        savepoint.rollback()
        logger.warning(
            f"IntegrityError upserting listing {listing.listing_id} "
            f"(source={listing_source}, product={product.product_id})"
        )
        return False


def _persist_listings(
    product_id: str,
    listings: list[Listing],
    *,
    force_is_sold: bool | None = None,
    llm_validation_results: dict[str, dict] | None = None,
    screenshot_paths: dict[str, str] | None = None,
    tracker: IngestionRun | None = None,
) -> int:
    """
    Persist listings with optional LLM validation results and screenshot paths.

    Args:
        product_id: Product template ID
        listings: List of listings to persist
        force_is_sold: Force sold status
        llm_validation_results: Dict mapping listing_id -> validation result
        screenshot_paths: Dict mapping listing_id -> screenshot path
    """
    if not listings:
        return 0

    valid_listings, validation_stats = validate_listings(listings)
    if tracker is not None:
        tracker.listings_missing_price = validation_stats.missing_price  # passed but price=None
        tracker.listings_rejected_title = validation_stats.rejected_title
    if validation_stats.rejected_price or validation_stats.rejected_title:
        logger.info(
            f"Validation: {validation_stats.passed}/{validation_stats.total} passed "
            f"({validation_stats.rejected_price} price, {validation_stats.rejected_title} title)"
        )

    processed_count = 0
    with SessionLocal() as db:
        product = db.query(ProductTemplate).filter(ProductTemplate.product_id == product_id).first()
        if not product:
            logger.warning(f"Product template {product_id} no longer exists; skipping persistence")
            return 0

        for listing in valid_listings:
            try:
                llm_result = None
                screenshot_path = None

                if llm_validation_results and listing.listing_id in llm_validation_results:
                    llm_result = llm_validation_results[listing.listing_id]

                if screenshot_paths and listing.listing_id in screenshot_paths:
                    screenshot_path = screenshot_paths[listing.listing_id]

                success = _upsert_listing(
                    db,
                    product,
                    listing,
                    force_is_sold=force_is_sold,
                    llm_validation_result=llm_result,
                    screenshot_path=screenshot_path,
                )
                if success:
                    processed_count += 1
            except SQLAlchemyError as exc:
                logger.error(
                    f"Failed to persist listing {listing.listing_id} for product {product_id}: {exc}"
                )

        product.last_ingested_at = datetime.now(UTC)
        db.commit()

    return processed_count


async def ingest_ebay_sold(product_id: str, limit: int = 50) -> dict[str, Any]:
    return {
        "status": "unsupported",
        "count": 0,
        "reason": "No verified eBay sold feed is configured",
    }


async def ingest_ebay_listings(product_id: str, limit: int = 50) -> dict[str, Any]:
    """
    Ingest active listings from eBay for a specific product.

    The eBay connector now returns parsed Listing objects directly,
    so no additional parsing is needed.
    """
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting eBay listings ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    try:
        with track_ingestion_run(product_id, "ebay", "ingest_ebay_listings") as run:
            listings = await fetch_ebay_listings(_compose_search_term(snapshot), limit)
            run.listings_fetched = len(listings) if listings else 0

            if not listings:
                run.status = "no_data"
                logger.info(f"No eBay listings found for product {snapshot.product_id}")
                return {"status": "no_data", "count": 0, "message": "No items found"}

            with SessionLocal() as db:
                product_template = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )
                if product_template:
                    make_transient(product_template)

            deduped = _dedupe_listings(listings)
            run.listings_deduped = len(deduped)
            filtered, stats, llm_results, screenshot_paths = await filter_listings_multi_stage(
                snapshot,
                deduped,
                product_template=product_template,
                enable_llm=True,
            )
            run.filtering_stats = filtering_stats_to_dict(stats)

            processed = _persist_listings(
                snapshot.product_id,
                filtered,
                force_is_sold=False,
                llm_validation_results=llm_results if llm_results else None,
                screenshot_paths=screenshot_paths if screenshot_paths else None,
                tracker=run,
            )
            run.listings_persisted = processed

            if processed:
                logger.info(
                    f"Ingested {processed} eBay active listings for product {snapshot.product_id}"
                )
                return {"status": "success", "count": processed}

            run.status = "no_data"
            logger.warning(f"No eBay listings matched filters for product {snapshot.product_id}")
            return {"status": "no_data", "count": 0}

    except Exception as exc:
        logger.error(f"Error in eBay listings ingestion for product {snapshot.product_id}: {exc}")
        return {"status": "error", "error": str(exc)}


async def ingest_leboncoin_listings(product_id: str, limit: int = 50) -> dict[str, Any]:
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting LeBonCoin listings ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    try:
        with track_ingestion_run(product_id, "leboncoin", "ingest_leboncoin_listings") as run:
            listings = await fetch_leboncoin_api_listings(_compose_search_term(snapshot), limit)
            run.listings_fetched = len(listings) if listings else 0

            if not listings:
                run.status = "no_data"
                return {"status": "no_data", "count": 0, "message": "No items found"}

            with SessionLocal() as db:
                product_template = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )
                if product_template:
                    make_transient(product_template)

            deduped = _dedupe_listings(listings)
            run.listings_deduped = len(deduped)
            filtered, stats, llm_results, screenshot_paths = await filter_listings_multi_stage(
                snapshot,
                deduped,
                product_template=product_template,
                enable_llm=True,
            )
            run.filtering_stats = filtering_stats_to_dict(stats)

            processed = _persist_listings(
                snapshot.product_id,
                filtered,
                force_is_sold=False,
                llm_validation_results=llm_results if llm_results else None,
                screenshot_paths=screenshot_paths if screenshot_paths else None,
                tracker=run,
            )
            run.listings_persisted = processed

            if processed:
                logger.info(
                    f"Ingested {processed} LeBonCoin listings for product {snapshot.product_id}"
                )
                return {"status": "success", "count": processed}

            run.status = "no_data"
            logger.warning(
                f"No LeBonCoin listings matched filters for product {snapshot.product_id}"
            )
            return {"status": "no_data", "count": 0}

    except Exception as exc:
        logger.error(
            f"Error in LeBonCoin listings ingestion for product {snapshot.product_id}: {exc}"
        )
        return {"status": "error", "error": str(exc)}


async def ingest_leboncoin_sold(product_id: str, limit: int = 50) -> dict[str, Any]:
    """No verified sold feed exists for LeBonCoin."""
    return {"status": "unsupported", "count": 0, "reason": "no_verified_sold_feed"}


async def ingest_vinted_listings(product_id: str, limit: int = 50) -> dict[str, Any]:
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting Vinted listings ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    try:
        with track_ingestion_run(product_id, "vinted", "ingest_vinted_listings") as run:
            listings = await fetch_vinted_listings(_compose_search_term(snapshot), limit)
            run.listings_fetched = len(listings) if listings else 0

            if not listings:
                run.status = "no_data"
                return {"status": "no_data", "count": 0, "message": "No items found"}

            with SessionLocal() as db:
                product_template = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )
                if product_template:
                    make_transient(product_template)

            deduped = _dedupe_listings(listings)
            run.listings_deduped = len(deduped)
            filtered, stats, llm_results, screenshot_paths = await filter_listings_multi_stage(
                snapshot,
                deduped,
                product_template=product_template,
                enable_llm=True,
            )
            run.filtering_stats = filtering_stats_to_dict(stats)

            processed = _persist_listings(
                snapshot.product_id,
                filtered,
                force_is_sold=False,
                llm_validation_results=llm_results if llm_results else None,
                screenshot_paths=screenshot_paths if screenshot_paths else None,
                tracker=run,
            )
            run.listings_persisted = processed

            if processed:
                logger.info(
                    f"Ingested {processed} Vinted listings for product {snapshot.product_id}"
                )
                return {"status": "success", "count": processed}

            run.status = "no_data"
            logger.warning(f"No Vinted listings matched filters for product {snapshot.product_id}")
            return {"status": "no_data", "count": 0}

    except Exception as exc:
        logger.error(f"Error in Vinted listings ingestion for product {snapshot.product_id}: {exc}")
        return {"status": "error", "error": str(exc)}


async def ingest_cashconverters_listings(product_id: str, limit: int = 50) -> dict[str, Any]:
    from ingestion.connectors.cashconverters import fetch_cashconverters_listings

    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}
    try:
        with track_ingestion_run(
            product_id, "cashconverters", "ingest_cashconverters_listings"
        ) as run:
            listings = await fetch_cashconverters_listings(
                _compose_search_term(snapshot), limit=limit
            )
            run.listings_fetched = len(listings)
            deduped = _dedupe_listings(listings)
            run.listings_deduped = len(deduped)
            with SessionLocal() as db:
                product = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )
                if product:
                    make_transient(product)
            filtered, stats, llm_results, screenshots = await filter_listings_multi_stage(
                snapshot, deduped, product_template=product, enable_llm=settings.llm_enabled
            )
            run.filtering_stats = filtering_stats_to_dict(stats)
            processed = _persist_listings(
                snapshot.product_id,
                filtered,
                force_is_sold=False,
                llm_validation_results=llm_results,
                screenshot_paths=screenshots,
                tracker=run,
            )
            run.listings_persisted = processed
            run.status = "success" if processed else "no_data"
            return {"status": run.status, "count": processed}
    except Exception as exc:
        logger.error("Cash Converters ingestion failed: {}", type(exc).__name__)
        return {"status": "error", "error": str(exc)}


def calculate_daily_metrics(product_id: str) -> dict[str, Any]:
    """Calculate daily metrics for a product"""
    with SessionLocal() as db:
        now_utc = datetime.now(UTC)

        # Get sold items from last 30 days
        thirty_days_ago = now_utc - timedelta(days=30)

        sold_items = (
            db.query(ListingObservation)
            .filter(
                and_(
                    ListingObservation.product_id == product_id,
                    ListingObservation.is_sold == True,
                    ListingObservation.evidence_type == "verified_sale",
                    ListingObservation.currency == "EUR",
                    ListingObservation.price > 0,
                    ListingObservation.observed_at >= thirty_days_ago,
                )
            )
            .all()
        )

        if not sold_items:
            return {
                "sold_count_7d": 0,
                "sold_count_30d": 0,
                "price_median": None,
                "price_std": None,
                "price_p25": None,
                "price_p75": None,
                "liquidity_score": 0.0,
                "trend_score": 0.0,
            }

        prices = [float(item.price) for item in sold_items if item.price]

        # Calculate PMN
        pmn_data = pmn_from_prices(prices)

        # Calculate liquidity score (based on number of sales in last 30 days)
        liquidity_score = min(len(sold_items) / 30.0, 1.0) * 100  # Match the 0-100 API scale

        # Calculate trend score (simple moving average comparison)
        recent_7d_cutoff = now_utc - timedelta(days=7)

        def _ensure_aware(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

        recent_7d = []
        for item in sold_items:
            observed_at = _ensure_aware(item.observed_at)
            if observed_at and observed_at >= recent_7d_cutoff:
                recent_7d.append(item)
        recent_7d_prices = [float(item.price) for item in recent_7d if item.price]

        if recent_7d_prices and len(prices) >= 7:
            recent_avg = sum(recent_7d_prices) / len(recent_7d_prices)
            overall_avg = sum(prices) / len(prices)
            trend_score = (recent_avg - overall_avg) / overall_avg if overall_avg > 0 else 0.0
        else:
            trend_score = 0.0

        return {
            "sold_count_7d": len(recent_7d),
            "sold_count_30d": len(sold_items),
            "price_median": pmn_data["pmn"],
            "price_std": pmn_data.get("pmn_high", 0) - pmn_data.get("pmn_low", 0)
            if pmn_data["pmn"]
            else 0,
            "price_p25": float(np.percentile(prices, 25)) if prices else None,
            "price_p75": float(np.percentile(prices, 75)) if prices else None,
            "liquidity_score": liquidity_score,
            "trend_score": trend_score,
        }


def update_product_metrics(product_id: str) -> None:
    """Update or create daily metrics for a product"""
    metrics_data = calculate_daily_metrics(product_id)

    with SessionLocal() as db:
        # Check if metrics already exist for today
        existing = (
            db.query(ProductDailyMetrics)
            .filter(
                and_(
                    ProductDailyMetrics.product_id == product_id,
                    ProductDailyMetrics.date == date.today(),
                )
            )
            .first()
        )

        if existing:
            # Update existing metrics
            for key, value in metrics_data.items():
                setattr(existing, key, value)
        else:
            # Create new metrics
            new_metrics = ProductDailyMetrics(
                product_id=product_id, date=date.today(), **metrics_data
            )
            db.add(new_metrics)

        db.commit()


async def run_full_ingestion(
    product_id: str, limits: dict[str, int] | None = None, sources: list[str] | None = None
) -> dict[str, Any]:
    from sqlalchemy import text

    with SessionLocal() as lock_db, lock_db.begin():
        locked = lock_db.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"ingest:{product_id}"},
        ).scalar()
        if not locked:
            return {"status": "skipped", "reason": "product_ingestion_running"}
        return await _run_full_ingestion(product_id, limits, sources)


async def _run_full_ingestion(
    product_id: str,
    limits: dict[str, int] | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Run full ingestion pipeline for a product template across selected providers."""

    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    if limits is None:
        limits = {
            "ebay_sold": 50,
            "ebay_listings": 50,
            "leboncoin_listings": 50,
            "leboncoin_sold": 50,
            "vinted_listings": 50,
            "cashconverters_listings": 50,
        }

    candidate_sources = sources or snapshot.providers or SUPPORTED_PROVIDERS
    logger.info(
        f"Starting ingestion for product '{snapshot.name}' ({snapshot.product_id}) providers={candidate_sources}"
    )

    results: dict[str, Any] = {
        "product_id": snapshot.product_id,
        "product_name": snapshot.name,
        "category": snapshot.category_name,
    }

    if "ebay" in candidate_sources:
        if "ebay_sold" in limits:
            results["ebay_sold"] = await ingest_ebay_sold(
                snapshot.product_id, limits.get("ebay_sold", 50)
            )
        if "ebay_listings" in limits:
            results["ebay_listings"] = await ingest_ebay_listings(
                snapshot.product_id, limits.get("ebay_listings", 50)
            )

    if "leboncoin" in candidate_sources:
        if "leboncoin_listings" in limits:
            results["leboncoin_listings"] = await ingest_leboncoin_listings(
                snapshot.product_id, limits.get("leboncoin_listings", 50)
            )
        if "leboncoin_sold" in limits:
            results["leboncoin_sold"] = await ingest_leboncoin_sold(
                snapshot.product_id, limits.get("leboncoin_sold", 50)
            )

    if "vinted" in candidate_sources:
        if "vinted_listings" in limits:
            results["vinted_listings"] = await ingest_vinted_listings(
                snapshot.product_id, limits.get("vinted_listings", 50)
            )

    if "cashconverters" in candidate_sources and "cashconverters_listings" in limits:
        results["cashconverters_listings"] = await ingest_cashconverters_listings(
            snapshot.product_id, limits.get("cashconverters_listings", 50)
        )

    try:
        update_product_metrics(snapshot.product_id)
    except Exception as exc:
        logger.error(f"Error updating metrics for product {snapshot.product_id}: {exc}")
        results.setdefault("warnings", []).append(f"metrics_update_failed: {snapshot.product_id}")

    logger.info(f"Full ingestion completed for product '{snapshot.name}' ({snapshot.product_id})")

    pipeline_result = await finish_product_pipeline(snapshot.product_id, candidate_sources)
    results["pipeline"] = pipeline_result
    results["status"] = pipeline_status(results)
    return results


def pipeline_status(results: dict[str, Any]) -> str:
    statuses = [
        value.get("status")
        for key, value in results.items()
        if key != "pipeline" and isinstance(value, dict) and "status" in value
    ]
    errors = sum(status in {"error", "partial"} for status in statuses)
    errors += results.get("pipeline", {}).get("status") in {"error", "partial"}
    successes = sum(status == "success" for status in statuses)
    if errors:
        return "partial" if successes else "error"
    return "success" if successes else "no_data"


async def finish_product_pipeline(product_id: str, sources: list[str]) -> dict[str, Any]:
    from ingestion.alert_engine import trigger_alerts
    from ingestion.composite_scoring import run_scoring_batch
    from ingestion.computation import compute_liquidity_score, compute_pmn_for_product
    from ingestion.connectors.ebay import fetch_detail as ebay_detail
    from ingestion.connectors.leboncoin_api import LeBonCoinAPIConnector
    from ingestion.connectors.vinted_api import VintedAPIConnector
    from ingestion.detail_fetch import fetch_and_persist_details
    from libs.common.models import ListingDetailORM

    result: dict[str, Any] = {"status": "success", "details": 0, "alerts": 0}
    with SessionLocal() as db:
        product = db.get(ProductTemplate, product_id)
        if product is None or not product.is_active:
            return {"status": "error", "reason": "inactive_product"}
        cutoff = datetime.now(UTC) - timedelta(minutes=settings.alert_freshness_minutes)
        for source in sources:
            observations = (
                db.query(ListingObservation)
                .outerjoin(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
                .filter(
                    ListingObservation.product_id == product_id,
                    ListingObservation.source == source,
                    ListingObservation.is_sold.is_(False),
                    ListingObservation.is_stale.is_(False),
                    ListingObservation.last_seen_at >= cutoff,
                    (ListingDetailORM.obs_id.is_(None))
                    | (ListingDetailORM.fetched_at < ListingObservation.updated_at)
                    | and_(settings.vision_enabled, ListingDetailORM.fetched_at < cutoff),
                )
                .order_by(ListingObservation.price.asc())
                .limit(20)
                .all()
            )
            if not observations or not settings.detail_fetch_enabled:
                continue
            try:
                if source == "ebay":
                    fetcher = ebay_detail
                elif source == "leboncoin":
                    import asyncio

                    connector = await asyncio.to_thread(LeBonCoinAPIConnector)
                    fetcher = connector.fetch_detail
                elif source == "vinted":
                    fetcher = VintedAPIConnector().fetch_detail
                else:
                    continue
                persisted = await fetch_and_persist_details(
                    db, observations, source, None, product.price_min, product.price_max, fetcher
                )
                result["details"] += persisted
                if persisted < len(observations):
                    result.setdefault("warnings", []).append(f"detail_incomplete:{source}")
            except Exception as exc:
                logger.warning("Detail pipeline failed for {}: {}", source, type(exc).__name__)
                result.setdefault("warnings", []).append(f"detail_failed:{source}")
        result["pmn"] = compute_pmn_for_product(product_id, db)
        liquidity = compute_liquidity_score(product_id, db)
        result["liquidity"] = liquidity
        if "error" not in liquidity:
            metrics = (
                db.query(ProductDailyMetrics)
                .filter(
                    ProductDailyMetrics.product_id == product_id,
                    ProductDailyMetrics.date == date.today(),
                )
                .first()
            )
            if metrics is None:
                metrics = ProductDailyMetrics(product_id=product_id, date=date.today())
                db.add(metrics)
            for field in ("liquidity_score", "sold_count_30d", "sold_count_7d"):
                setattr(metrics, field, liquidity[field])
            db.commit()
    if settings.vision_enabled:
        from ingestion.listing_vision import run_listing_vision_batch

        result["vision"] = await run_listing_vision_batch(product_id=product_id)
        if result["vision"].get("status") == "partial":
            result.setdefault("warnings", []).append("vision_incomplete")
    result["scoring"] = await run_scoring_batch(product_id=product_id)
    with SessionLocal() as db:
        product = db.get(ProductTemplate, product_id)
        listings = (
            db.query(ListingObservation)
            .filter(
                ListingObservation.product_id == product_id,
                ListingObservation.is_sold.is_(False),
                ListingObservation.is_stale.is_(False),
                ListingObservation.last_seen_at >= cutoff,
            )
            .order_by(ListingObservation.last_seen_at.desc())
            .limit(200)
            .all()
        )
        metrics = (
            db.query(ProductDailyMetrics)
            .filter(ProductDailyMetrics.product_id == product_id)
            .order_by(ProductDailyMetrics.date.desc())
            .first()
        )
        events = await trigger_alerts(
            [
                {"listing": listing, "product_template": product, "metrics": metrics}
                for listing in listings
            ],
            db,
        )
        result["alerts"] = sum(event.delivery_status == "sent" for event in events)
    if (
        result.get("warnings")
        or "error" in result["liquidity"]
        or result["scoring"].get("status") == "error"
        or result["pmn"].get("status") == "error"
    ):
        result["status"] = "partial"
    return result

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ingestion.connectors.ebay import fetch_ebay_listings, fetch_ebay_sold
from ingestion.connectors.leboncoin_api import (
    fetch_leboncoin_api_listings,
    fetch_leboncoin_api_sold,
)
from ingestion.connectors.vinted import fetch_vinted_listings
from ingestion.constants import SUPPORTED_PROVIDERS
from ingestion.filtering import filter_listings_multi_stage
from ingestion.pricing import pmn_from_prices
from ingestion.run_tracker import filtering_stats_to_dict, track_ingestion_run
from ingestion.validation import validate_listings
from libs.common.db import SessionLocal
from libs.common.models import (
    Listing,
    ListingObservation,
    ProductDailyMetrics,
    ProductTemplate,
)


@dataclass
class ProductTemplateSnapshot:
    product_id: str
    name: str
    description: str | None
    search_query: str
    category_id: str
    category_name: str | None
    brand: str | None
    price_min: float | None
    price_max: float | None
    providers: list[str]
    words_to_avoid: list[str]
    enable_llm_validation: bool
    is_active: bool


def _decimal_to_float(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


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
    listing_source = listing.source
    now_utc = datetime.now(timezone.utc)

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
            observed_at = observed_at.replace(tzinfo=timezone.utc)

        is_sold = force_is_sold if force_is_sold is not None else listing.is_sold

        if existing:
            existing.price = listing.price
            existing.title = listing.title
            existing.currency = listing.currency
            existing.condition = listing.condition_raw
            existing.is_sold = is_sold
            existing.seller_rating = listing.seller_rating
            existing.shipping_cost = listing.shipping_cost
            existing.location = listing.location
            existing.observed_at = observed_at
            existing.url = listing.url
            existing.last_seen_at = now_utc

            # Update LLM validation fields if provided
            if llm_validation_result is not None:
                existing.llm_validated = True
                existing.llm_validation_result = llm_validation_result
                existing.llm_validated_at = now_utc
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
                location=listing.location,
                observed_at=observed_at,
                url=listing.url,
                last_seen_at=now_utc,
                llm_validated=llm_validation_result is not None,
                llm_validation_result=llm_validation_result,
                llm_validated_at=now_utc if llm_validation_result else None,
                screenshot_path=screenshot_path,
            )
            db.add(observation)

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

                _upsert_listing(
                    db,
                    product,
                    listing,
                    force_is_sold=force_is_sold,
                    llm_validation_result=llm_result,
                    screenshot_path=screenshot_path,
                )
                processed_count += 1
            except Exception as exc:
                logger.error(
                    f"Failed to persist listing {listing.listing_id} for product {product_id}: {exc}"
                )

        product.last_ingested_at = datetime.now(timezone.utc)
        db.commit()

    return processed_count


async def ingest_ebay_sold(product_id: str, limit: int = 50) -> dict[str, Any]:
    """
    Ingest sold items from eBay for a specific product.

    The eBay connector now returns parsed Listing objects directly,
    so no additional parsing is needed.
    """
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting eBay sold ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    with track_ingestion_run(product_id, "ebay", "ingest_ebay_sold") as run:
        try:
            listings = await fetch_ebay_sold(_compose_search_term(snapshot), limit)
            run.listings_fetched = len(listings) if listings else 0

            if not listings:
                run.status = "no_data"
                logger.info(f"No eBay sold items found for product {snapshot.product_id}")
                return {"status": "success", "count": 0, "message": "No items found"}

            with SessionLocal() as db:
                product_template = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )

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
                force_is_sold=True,
                llm_validation_results=llm_results if llm_results else None,
                screenshot_paths=screenshot_paths if screenshot_paths else None,
            )
            run.listings_persisted = processed

            if processed:
                logger.info(
                    f"Ingested {processed} eBay sold listings for product {snapshot.product_id}"
                )
                return {"status": "success", "count": processed}

            run.status = "no_data"
            logger.warning(
                f"No eBay sold listings matched filters for product {snapshot.product_id}"
            )
            return {"status": "no_data", "count": 0}

        except Exception as exc:
            run.status = "error"
            run.error_message = str(exc)[:2000]
            logger.error(
                f"Error in eBay sold ingestion for product {snapshot.product_id}: {exc}"
            )
            return {"status": "error", "error": str(exc)}


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

    with track_ingestion_run(product_id, "ebay", "ingest_ebay_listings") as run:
        try:
            listings = await fetch_ebay_listings(_compose_search_term(snapshot), limit)
            run.listings_fetched = len(listings) if listings else 0

            if not listings:
                run.status = "no_data"
                logger.info(f"No eBay listings found for product {snapshot.product_id}")
                return {"status": "success", "count": 0, "message": "No items found"}

            with SessionLocal() as db:
                product_template = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )

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
            )
            run.listings_persisted = processed

            if processed:
                logger.info(
                    f"Ingested {processed} eBay active listings for product {snapshot.product_id}"
                )
                return {"status": "success", "count": processed}

            run.status = "no_data"
            logger.warning(
                f"No eBay listings matched filters for product {snapshot.product_id}"
            )
            return {"status": "no_data", "count": 0}

        except Exception as exc:
            run.status = "error"
            run.error_message = str(exc)[:2000]
            logger.error(
                f"Error in eBay listings ingestion for product {snapshot.product_id}: {exc}"
            )
            return {"status": "error", "error": str(exc)}


async def ingest_leboncoin_listings(product_id: str, limit: int = 50) -> dict[str, Any]:
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting LeBonCoin listings ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    with track_ingestion_run(product_id, "leboncoin", "ingest_leboncoin_listings") as run:
        try:
            listings = await fetch_leboncoin_api_listings(_compose_search_term(snapshot), limit)
            run.listings_fetched = len(listings) if listings else 0

            if not listings:
                run.status = "no_data"
                return {"status": "success", "count": 0, "message": "No items found"}

            with SessionLocal() as db:
                product_template = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )

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
            run.status = "error"
            run.error_message = str(exc)[:2000]
            logger.error(
                f"Error in LeBonCoin listings ingestion for product {snapshot.product_id}: {exc}"
            )
            return {"status": "error", "error": str(exc)}


async def ingest_leboncoin_sold(product_id: str, limit: int = 50) -> dict[str, Any]:
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting LeBonCoin 'sold' ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    with track_ingestion_run(product_id, "leboncoin", "ingest_leboncoin_sold") as run:
        try:
            listings = await fetch_leboncoin_api_sold(_compose_search_term(snapshot), limit)
            run.listings_fetched = len(listings) if listings else 0

            if not listings:
                run.status = "no_data"
                return {"status": "success", "count": 0, "message": "No items found"}

            with SessionLocal() as db:
                product_template = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )

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
                force_is_sold=True,
                llm_validation_results=llm_results if llm_results else None,
                screenshot_paths=screenshot_paths if screenshot_paths else None,
            )
            run.listings_persisted = processed

            if processed:
                logger.info(
                    f"Ingested {processed} LeBonCoin 'sold' listings for product {snapshot.product_id}"
                )
                return {"status": "success", "count": processed}

            run.status = "no_data"
            logger.warning(
                f"No LeBonCoin 'sold' listings matched filters for product {snapshot.product_id}"
            )
            return {"status": "no_data", "count": 0}

        except Exception as exc:
            run.status = "error"
            run.error_message = str(exc)[:2000]
            logger.error(
                f"Error in LeBonCoin 'sold' ingestion for product {snapshot.product_id}: {exc}"
            )
            return {"status": "error", "error": str(exc)}


async def ingest_vinted_listings(product_id: str, limit: int = 50) -> dict[str, Any]:
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting Vinted listings ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    with track_ingestion_run(product_id, "vinted", "ingest_vinted_listings") as run:
        try:
            listings = await fetch_vinted_listings(_compose_search_term(snapshot), limit)
            run.listings_fetched = len(listings) if listings else 0

            if not listings:
                run.status = "no_data"
                return {"status": "success", "count": 0, "message": "No items found"}

            with SessionLocal() as db:
                product_template = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.product_id == snapshot.product_id)
                    .first()
                )

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
            )
            run.listings_persisted = processed

            if processed:
                logger.info(
                    f"Ingested {processed} Vinted listings for product {snapshot.product_id}"
                )
                return {"status": "success", "count": processed}

            run.status = "no_data"
            logger.warning(
                f"No Vinted listings matched filters for product {snapshot.product_id}"
            )
            return {"status": "no_data", "count": 0}

        except Exception as exc:
            run.status = "error"
            run.error_message = str(exc)[:2000]
            logger.error(
                f"Error in Vinted listings ingestion for product {snapshot.product_id}: {exc}"
            )
            return {"status": "error", "error": str(exc)}


def calculate_daily_metrics(product_id: str) -> dict[str, Any]:
    """Calculate daily metrics for a product"""
    with SessionLocal() as db:
        today = date.today()
        now_utc = datetime.now(timezone.utc)

        # Get sold items from last 30 days
        thirty_days_ago = now_utc - timedelta(days=30)

        sold_items = (
            db.query(ListingObservation)
            .filter(
                and_(
                    ListingObservation.product_id == product_id,
                    ListingObservation.is_sold == True,
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
        liquidity_score = min(len(sold_items) / 30.0, 1.0)  # Normalize to 0-1

        # Calculate trend score (simple moving average comparison)
        recent_7d_cutoff = now_utc - timedelta(days=7)

        def _ensure_aware(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

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
            "price_p25": min(prices) if prices else None,
            "price_p75": max(prices) if prices else None,
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
        results["ebay_sold"] = await ingest_ebay_sold(
            snapshot.product_id, limits.get("ebay_sold", 50)
        )
        results["ebay_listings"] = await ingest_ebay_listings(
            snapshot.product_id, limits.get("ebay_listings", 50)
        )

    if "leboncoin" in candidate_sources:
        results["leboncoin_listings"] = await ingest_leboncoin_listings(
            snapshot.product_id, limits.get("leboncoin_listings", 50)
        )
        results["leboncoin_sold"] = await ingest_leboncoin_sold(
            snapshot.product_id, limits.get("leboncoin_sold", limits.get("leboncoin_listings", 50))
        )

    if "vinted" in candidate_sources:
        results["vinted_listings"] = await ingest_vinted_listings(
            snapshot.product_id, limits.get("vinted_listings", 50)
        )

    try:
        update_product_metrics(snapshot.product_id)
    except Exception as exc:
        logger.error(f"Error updating metrics for product {snapshot.product_id}: {exc}")
        results.setdefault("warnings", []).append(f"metrics_update_failed: {snapshot.product_id}")

    logger.info(f"Full ingestion completed for product '{snapshot.name}' ({snapshot.product_id})")

    # Trigger alerts for opportunities after ingestion
    try:
        from ingestion.alert_engine import trigger_alerts
        from libs.common.models import MarketPriceNormal, ProductDailyMetrics

        with SessionLocal() as db:
            # Get opportunities (listings below PMN)
            pmn_data = (
                db.query(MarketPriceNormal)
                .filter(MarketPriceNormal.product_id == snapshot.product_id)
                .first()
            )

            metrics = (
                db.query(ProductDailyMetrics)
                .filter(ProductDailyMetrics.product_id == snapshot.product_id)
                .order_by(ProductDailyMetrics.date.desc())
                .first()
            )

            if pmn_data and pmn_data.pmn:
                # Get active listings below PMN
                opportunities_list = (
                    db.query(ListingObservation)
                    .filter(
                        ListingObservation.product_id == snapshot.product_id,
                        ListingObservation.is_sold == False,
                        ListingObservation.price.isnot(None),
                        ListingObservation.price < pmn_data.pmn,
                    )
                    .limit(200)
                    .all()
                )

                if opportunities_list:
                    product_template = (
                        db.query(ProductTemplate)
                        .filter(ProductTemplate.product_id == snapshot.product_id)
                        .first()
                    )

                    opportunities = [
                        {
                            "listing": listing,
                            "product_template": product_template,
                            "pmn_data": pmn_data,
                            "metrics": metrics,
                        }
                        for listing in opportunities_list
                    ]

                    alert_events = await trigger_alerts(opportunities, db)
                    if alert_events:
                        logger.info(
                            f"Triggered {len(alert_events)} alerts for product {snapshot.product_id}"
                        )
                        results["alerts_triggered"] = len(alert_events)
    except Exception as exc:
        logger.error(f"Error triggering alerts after ingestion: {exc}")
        # Don't fail ingestion if alerts fail
        results.setdefault("warnings", []).append(f"alert_trigger_failed: {exc}")

    results["status"] = "success"
    return results

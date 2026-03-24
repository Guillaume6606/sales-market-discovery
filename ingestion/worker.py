from datetime import UTC, datetime, timedelta
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import and_, update
from sqlalchemy.orm.session import make_transient

from ingestion.alert_engine import trigger_alerts
from ingestion.computation import (
    compute_all_product_metrics,
    compute_liquidity_score,
    compute_pmn_for_product,
)
from ingestion.enrichment import run_enrichment_batch
from ingestion.ingestion import (
    ingest_ebay_listings,
    ingest_ebay_sold,
    ingest_leboncoin_listings,
    ingest_leboncoin_sold,
    ingest_vinted_listings,
    run_full_ingestion,
)
from libs.common.db import SessionLocal
from libs.common.llm_service import assess_listing_relevance
from libs.common.log import logger
from libs.common.models import (
    ConnectorAudit,
    IngestionRun,
    ListingObservation,
    MarketPriceNormal,
    ProductDailyMetrics,
    ProductTemplate,
)
from libs.common.screenshot_service import capture_listing_screenshot
from libs.common.settings import settings
from libs.common.telegram_service import send_system_alert


async def ping(ctx):
    logger.info("Worker alive.")


def _active_product_ids(provider: str | None = None) -> list[str]:
    with SessionLocal() as db:
        products = db.query(ProductTemplate).filter(ProductTemplate.is_active == True).all()

    product_ids: list[str] = []
    for product in products:
        allowed_providers = product.providers or []
        if provider and allowed_providers and provider not in allowed_providers:
            continue
        product_ids.append(str(product.product_id))

    if provider:
        logger.info(
            "Found %d active product templates for provider '%s'",
            len(product_ids),
            provider,
        )
    else:
        logger.info("Found %d active product templates", len(product_ids))

    return product_ids


async def scheduled_ebay_ingestion(ctx):
    """Scheduled ingestion for all active products targeting eBay."""
    product_ids = _active_product_ids("ebay")
    logger.info("Starting scheduled eBay ingestion for %d products", len(product_ids))

    results: dict[str, dict[str, Any]] = {}
    for product_id in product_ids:
        try:
            result = await run_full_ingestion(
                product_id,
                {"ebay_sold": 20, "ebay_listings": 20},
                sources=["ebay"],
            )
            results[product_id] = result
            logger.info(f"Completed scheduled eBay ingestion for {product_id}: {result}")
        except Exception as exc:
            logger.error(f"Error in scheduled eBay ingestion for {product_id}: {exc}")
            results[product_id] = {"status": "error", "error": str(exc)}

    if settings.audit_enabled:
        try:
            pool = ctx.get("redis") or ctx.get("pool")
            if pool:
                await pool.enqueue_job("audit_ingestion_sample", source="ebay")
        except Exception as exc:
            logger.warning("Failed to enqueue audit task: %s", exc)

    return results


async def scheduled_leboncoin_ingestion(ctx):
    """Scheduled ingestion for all active products targeting LeBonCoin."""
    product_ids = _active_product_ids("leboncoin")
    logger.info("Starting scheduled LeBonCoin ingestion for %d products", len(product_ids))

    results: dict[str, dict[str, Any]] = {}
    for product_id in product_ids:
        try:
            result = await run_full_ingestion(
                product_id,
                {"leboncoin_listings": 20, "leboncoin_sold": 20},
                sources=["leboncoin"],
            )
            results[product_id] = result
            logger.info(f"Completed scheduled LeBonCoin ingestion for {product_id}: {result}")
        except Exception as exc:
            logger.error(f"Error in scheduled LeBonCoin ingestion for {product_id}: {exc}")
            results[product_id] = {"status": "error", "error": str(exc)}

    if settings.audit_enabled:
        try:
            pool = ctx.get("redis") or ctx.get("pool")
            if pool:
                await pool.enqueue_job("audit_ingestion_sample", source="leboncoin")
        except Exception as exc:
            logger.warning("Failed to enqueue audit task: %s", exc)

    return results


async def scheduled_vinted_ingestion(ctx):
    """Scheduled ingestion for all active products targeting Vinted."""
    product_ids = _active_product_ids("vinted")
    logger.info("Starting scheduled Vinted ingestion for %d products", len(product_ids))

    results: dict[str, dict[str, Any]] = {}
    for product_id in product_ids:
        try:
            result = await run_full_ingestion(
                product_id,
                {"vinted_listings": 20},
                sources=["vinted"],
            )
            results[product_id] = result
            logger.info(f"Completed scheduled Vinted ingestion for {product_id}: {result}")
        except Exception as exc:
            logger.error(f"Error in scheduled Vinted ingestion for {product_id}: {exc}")
            results[product_id] = {"status": "error", "error": str(exc)}

    if settings.audit_enabled:
        try:
            pool = ctx.get("redis") or ctx.get("pool")
            if pool:
                await pool.enqueue_job("audit_ingestion_sample", source="vinted")
        except Exception as exc:
            logger.warning("Failed to enqueue audit task: %s", exc)

    return results


async def trigger_ebay_sold_ingestion(ctx, product_id: str, limit: int = 50):
    """Trigger eBay sold ingestion for a specific product template."""
    logger.info(f"Triggering eBay sold items ingestion for product {product_id}")
    result = await ingest_ebay_sold(product_id, limit)
    logger.info(f"Completed sold items ingestion for {product_id}: {result}")
    return result


async def trigger_ebay_listings_ingestion(ctx, product_id: str, limit: int = 50):
    """Trigger eBay listings ingestion for a specific product template."""
    logger.info(f"Triggering eBay listings ingestion for product {product_id}")
    result = await ingest_ebay_listings(product_id, limit)
    logger.info(f"Completed listings ingestion for {product_id}: {result}")
    return result


async def trigger_full_ingestion(
    ctx,
    product_id: str,
    sold_limit: int = 50,
    listings_limit: int = 50,
    sources=None,
):
    """Trigger full ingestion pipeline for a specific product template."""
    if sources is None:
        sources = ["ebay", "leboncoin", "vinted"]
    logger.info(f"Triggering full ingestion for product {product_id} from sources: {sources}")
    result = await run_full_ingestion(
        product_id,
        {
            "ebay_sold": sold_limit,
            "ebay_listings": listings_limit,
            "leboncoin_listings": listings_limit,
            "leboncoin_sold": sold_limit,
            "vinted_listings": listings_limit,
        },
        sources,
    )
    logger.info(f"Completed full ingestion for {product_id}: {result}")
    return result


async def trigger_leboncoin_listings_ingestion(ctx, product_id: str, limit: int = 50):
    """Trigger LeBonCoin listings ingestion for a specific product template."""
    logger.info(f"Triggering LeBonCoin listings ingestion for product {product_id}")
    result = await ingest_leboncoin_listings(product_id, limit)
    logger.info(f"Completed LeBonCoin listings ingestion for {product_id}: {result}")
    return result


async def trigger_leboncoin_sold_ingestion(ctx, product_id: str, limit: int = 50):
    """Trigger LeBonCoin 'sold' ingestion for a specific product template."""
    logger.info(f"Triggering LeBonCoin 'sold' ingestion for product {product_id}")
    result = await ingest_leboncoin_sold(product_id, limit)
    logger.info(f"Completed LeBonCoin 'sold' ingestion for {product_id}: {result}")
    return result


async def trigger_vinted_listings_ingestion(ctx, product_id: str, limit: int = 50):
    """Trigger Vinted listings ingestion for a specific product template."""
    logger.info(f"Triggering Vinted listings ingestion for product {product_id}")
    result = await ingest_vinted_listings(product_id, limit)
    logger.info(f"Completed Vinted listings ingestion for {product_id}: {result}")
    return result


# ============================================================================
# COMPUTATION WORKER TASKS
# ============================================================================


async def scheduled_computation(ctx):
    """
    Scheduled task to compute PMN and metrics for all active products.
    Runs daily after ingestion completes.
    """
    product_ids = _active_product_ids()
    logger.info(f"Starting scheduled computation for {len(product_ids)} products")

    try:
        # Run batch computation
        result = compute_all_product_metrics(product_ids)
        logger.info(f"Scheduled computation completed: {result}")
        return result
    except Exception as exc:
        logger.error(f"Error in scheduled computation: {exc}", exc_info=True)
        return {"status": "error", "error": str(exc)}


async def trigger_product_computation(ctx, product_id: str):
    """
    Trigger PMN and metrics computation for a specific product.

    Args:
        ctx: ARQ context
        product_id: UUID of product template

    Returns:
        Dict with computation results
    """
    logger.info(f"Triggering computation for product {product_id}")

    try:
        with SessionLocal() as db:
            # Compute PMN
            pmn_result = compute_pmn_for_product(product_id, db)
            logger.info(f"PMN computation result for {product_id}: {pmn_result.get('status')}")

            # Compute liquidity
            liquidity_result = compute_liquidity_score(product_id, db)
            logger.info(
                f"Liquidity score for {product_id}: {liquidity_result.get('liquidity_score')}"
            )

            return {
                "status": "success",
                "product_id": product_id,
                "pmn": pmn_result,
                "liquidity": liquidity_result,
            }

    except Exception as exc:
        logger.error(f"Error in product computation for {product_id}: {exc}", exc_info=True)
        return {"status": "error", "product_id": product_id, "error": str(exc)}


async def trigger_batch_computation(ctx, product_ids: list[str] | None = None):
    """
    Trigger computation for multiple products.

    Args:
        ctx: ARQ context
        product_ids: Optional list of product IDs (if None, processes all active)

    Returns:
        Dict with batch computation statistics
    """
    if product_ids is None:
        product_ids = _active_product_ids()

    logger.info(f"Triggering batch computation for {len(product_ids)} products")

    try:
        result = compute_all_product_metrics(product_ids)
        logger.info(f"Batch computation completed: {result}")
        return result
    except Exception as exc:
        logger.error(f"Error in batch computation: {exc}", exc_info=True)
        return {"status": "error", "error": str(exc)}


# ============================================================================
# LLM VALIDATION & SCREENSHOT WORKER TASKS
# ============================================================================


async def validate_listing_with_llm(ctx, obs_id: int):
    """
    Validate a listing using LLM service.

    Args:
        ctx: ARQ context
        obs_id: Observation ID of the listing

    Returns:
        Dict with validation result
    """
    logger.info(f"Triggering LLM validation for listing {obs_id}")

    try:
        with SessionLocal() as db:
            listing = (
                db.query(ListingObservation).filter(ListingObservation.obs_id == obs_id).first()
            )

            if not listing:
                return {"status": "error", "error": "Listing not found"}

            product_template = (
                db.query(ProductTemplate)
                .filter(ProductTemplate.product_id == listing.product_id)
                .first()
            )

            if not product_template:
                return {"status": "error", "error": "Product template not found"}

            if not product_template.enable_llm_validation:
                return {
                    "status": "skipped",
                    "reason": "LLM validation not enabled for this product",
                }

            # Capture screenshot if URL available
            screenshot_path = None
            if listing.url:
                try:
                    screenshot_path = await capture_listing_screenshot(
                        listing.url, listing.listing_id, listing.source
                    )
                except Exception as e:
                    logger.warning(f"Failed to capture screenshot: {e}")

            # Run LLM validation
            words_to_avoid = product_template.words_to_avoid or []
            from libs.common.models import Listing as ListingModel

            listing_obj = ListingModel(
                source=listing.source,
                listing_id=listing.listing_id,
                title=listing.title or "",
                price=float(listing.price) if listing.price else None,
                currency=listing.currency or "EUR",
                condition_raw=listing.condition,
                condition_norm=None,
                location=listing.location,
                seller_rating=float(listing.seller_rating) if listing.seller_rating else None,
                shipping_cost=float(listing.shipping_cost) if listing.shipping_cost else None,
                observed_at=listing.observed_at or datetime.now(UTC),
                is_sold=listing.is_sold or False,
                url=listing.url,
            )

            validation_result = assess_listing_relevance(
                listing_obj, screenshot_path, product_template, words_to_avoid
            )

            # Update listing with validation result
            listing.llm_validated = True
            listing.llm_validation_result = validation_result
            listing.llm_validated_at = datetime.now(UTC)
            if screenshot_path:
                listing.screenshot_path = screenshot_path

            db.commit()

            logger.info(
                f"LLM validation completed for listing {obs_id}: {validation_result.get('is_relevant')}"
            )
            return {
                "status": "success",
                "obs_id": obs_id,
                "validation_result": validation_result,
            }

    except Exception as exc:
        logger.error(f"Error in LLM validation for listing {obs_id}: {exc}", exc_info=True)
        return {"status": "error", "error": str(exc)}


async def capture_listing_screenshot_task(ctx, obs_id: int):
    """
    Capture screenshot for a listing.

    Args:
        ctx: ARQ context
        obs_id: Observation ID of the listing

    Returns:
        Dict with screenshot path
    """
    logger.info(f"Triggering screenshot capture for listing {obs_id}")

    try:
        with SessionLocal() as db:
            listing = (
                db.query(ListingObservation).filter(ListingObservation.obs_id == obs_id).first()
            )

            if not listing:
                return {"status": "error", "error": "Listing not found"}

            if not listing.url:
                return {"status": "error", "error": "Listing URL not available"}

            screenshot_path = await capture_listing_screenshot(
                listing.url, listing.listing_id, listing.source
            )

            if screenshot_path:
                listing.screenshot_path = screenshot_path
                db.commit()
                logger.info(f"Screenshot captured for listing {obs_id}: {screenshot_path}")
                return {
                    "status": "success",
                    "obs_id": obs_id,
                    "screenshot_path": screenshot_path,
                }
            else:
                return {"status": "error", "error": "Failed to capture screenshot"}

    except Exception as exc:
        logger.error(f"Error capturing screenshot for listing {obs_id}: {exc}", exc_info=True)
        return {"status": "error", "error": str(exc)}


async def process_opportunity_alerts(ctx, product_id: str):
    """
    Process and trigger alerts for opportunities in a product.

    Args:
        ctx: ARQ context
        product_id: Product template ID

    Returns:
        Dict with alert statistics
    """
    logger.info(f"Processing opportunity alerts for product {product_id}")

    try:
        with SessionLocal() as db:
            product_template = (
                db.query(ProductTemplate).filter(ProductTemplate.product_id == product_id).first()
            )

            if not product_template:
                return {"status": "error", "error": "Product template not found"}

            pmn_data = (
                db.query(MarketPriceNormal)
                .filter(MarketPriceNormal.product_id == product_id)
                .first()
            )

            if not pmn_data or not pmn_data.pmn:
                return {"status": "skipped", "reason": "PMN not computed for this product"}

            metrics = (
                db.query(ProductDailyMetrics)
                .filter(ProductDailyMetrics.product_id == product_id)
                .order_by(ProductDailyMetrics.date.desc())
                .first()
            )

            # Get opportunities (listings below PMN)
            opportunities_list = (
                db.query(ListingObservation)
                .filter(
                    ListingObservation.product_id == product_id,
                    ListingObservation.is_sold == False,
                    ListingObservation.is_stale == False,
                    ListingObservation.price.isnot(None),
                    ListingObservation.price < pmn_data.pmn,
                )
                .limit(200)
                .all()
            )

            if not opportunities_list:
                return {"status": "success", "alerts_triggered": 0, "opportunities_found": 0}

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

            logger.info(f"Triggered {len(alert_events)} alerts for product {product_id}")
            return {
                "status": "success",
                "alerts_triggered": len(alert_events),
                "opportunities_found": len(opportunities_list),
            }

    except Exception as exc:
        logger.error(
            f"Error processing opportunity alerts for product {product_id}: {exc}", exc_info=True
        )
        return {"status": "error", "error": str(exc)}


async def mark_stale_listings(ctx: dict) -> dict:
    """Mark listings as stale if not seen within stale_listing_days."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.stale_listing_days)

    with SessionLocal() as db:
        result = db.execute(
            update(ListingObservation)
            .where(
                and_(
                    ListingObservation.last_seen_at < cutoff,
                    ListingObservation.is_sold == False,
                    ListingObservation.is_stale == False,
                )
            )
            .values(is_stale=True)
        )
        count = result.rowcount
        db.commit()

    logger.info(f"Marked {count} listings as stale (not seen since {cutoff.isoformat()})")
    return {"marked_stale": count}


async def check_system_health(ctx: dict) -> dict:
    """Check for stale products and failing connectors, send Telegram alert if issues found."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.stale_product_hours)

    with SessionLocal() as db:
        # Stale products
        stale_products_raw = (
            db.query(ProductTemplate)
            .filter(
                ProductTemplate.is_active == True,
            )
            .all()
        )

        stale_products = []
        for p in stale_products_raw:
            if p.last_ingested_at is None:
                stale_products.append({"name": p.name, "hours_since_ingestion": None})
            else:
                ts = p.last_ingested_at
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts < cutoff:
                    hours = (now - ts).total_seconds() / 3600
                    stale_products.append({"name": p.name, "hours_since_ingestion": hours})

        # Failing connectors
        sources = (
            db.query(IngestionRun.source).filter(IngestionRun.source.isnot(None)).distinct().all()
        )

        failing_connectors = []
        threshold = settings.connector_failure_threshold
        for (source,) in sources:
            recent_runs = (
                db.query(IngestionRun)
                .filter(IngestionRun.source == source)
                .order_by(IngestionRun.started_at.desc())
                .limit(threshold)
                .all()
            )
            if len(recent_runs) >= threshold and all(r.status == "error" for r in recent_runs):
                last_error = recent_runs[0].error_message or "unknown"
                failing_connectors.append(
                    {
                        "name": source,
                        "consecutive_failures": len(recent_runs),
                        "last_error": last_error,
                    }
                )

    if stale_products or failing_connectors:
        await send_system_alert(
            "System Health Alert",
            stale_products,
            failing_connectors,
        )

    logger.info(
        f"System health check: {len(stale_products)} stale products, "
        f"{len(failing_connectors)} failing connectors"
    )
    return {
        "stale_products": len(stale_products),
        "failing_connectors": len(failing_connectors),
    }


async def audit_ingestion_sample(
    ctx: dict, source: str, ingestion_run_id: str | None = None
) -> dict:
    """Sample recent listings from an ingestion run and audit them."""
    if not settings.audit_enabled:
        return {"status": "disabled"}

    if not settings.llm_enabled:
        logger.warning("audit_enabled=True but llm_enabled=False — skipping audit (no LLM judge)")
        return {"status": "skipped", "reason": "llm_disabled"}

    with SessionLocal() as db:
        # Exclude listings already audited in the last 24h to avoid re-auditing
        recently_audited = (
            db.query(ConnectorAudit.obs_id)
            .filter(ConnectorAudit.audited_at >= datetime.now(UTC) - timedelta(hours=24))
            .subquery()
        )
        query = (
            db.query(ListingObservation)
            .filter(
                ListingObservation.source == source,
                ListingObservation.url.isnot(None),
                ~ListingObservation.obs_id.in_(recently_audited),
            )
            .order_by(ListingObservation.last_seen_at.desc())
            .limit(settings.audit_sample_size)
        )
        listings = query.all()

        if not listings:
            return {"status": "no_listings", "source": source}

        for listing in listings:
            make_transient(listing)

    from ingestion.audit import audit_listings

    records = await audit_listings(
        listings,
        audit_mode="continuous",
        ingestion_run_id=ingestion_run_id,
    )

    with SessionLocal() as db:
        for r in records:
            db.add(r)
        db.commit()

    from ingestion.audit import compute_connector_accuracy

    accuracy_data = compute_connector_accuracy(records)
    for src, data in accuracy_data.items():
        if data["status"] == "red":
            try:
                from libs.common.telegram_service import send_connector_quality_alert

                await send_connector_quality_alert(src, data)
            except Exception as exc:
                logger.error("Failed to send quality alert: %s", exc)

    return {
        "status": "success",
        "source": source,
        "audited": len(records),
        "accuracy": {s: d["accuracy"] for s, d in accuracy_data.items()},
    }


async def run_on_demand_audit(
    ctx: dict,
    connector: str | None = None,
    sample_size: int = 20,
    product_id: str | None = None,
) -> dict:
    """Run an on-demand connector audit."""
    import redis as redis_lib

    try:
        with SessionLocal() as db:
            query = db.query(ListingObservation).filter(
                ListingObservation.url.isnot(None),
            )
            if connector:
                query = query.filter(ListingObservation.source == connector)
            if product_id:
                query = query.filter(ListingObservation.product_id == product_id)

            listings = (
                query.order_by(ListingObservation.last_seen_at.desc()).limit(sample_size).all()
            )
            for listing in listings:
                make_transient(listing)

        if not listings:
            return {"status": "no_listings"}

        from ingestion.audit import audit_listings, compute_connector_accuracy

        records = await audit_listings(listings, audit_mode="on_demand")

        with SessionLocal() as db:
            for r in records:
                db.add(r)
            db.commit()

        accuracy = compute_connector_accuracy(records)
        return {"status": "success", "audited": len(records), "accuracy": accuracy}

    finally:
        try:
            with redis_lib.from_url(settings.redis_url) as r:
                r.delete("audit:on_demand:running")
        except Exception as cleanup_exc:  # noqa: BLE001
            logger.warning("Failed to clear on-demand audit lock: %s", cleanup_exc)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [
        ping,
        scheduled_ebay_ingestion,
        scheduled_leboncoin_ingestion,
        scheduled_vinted_ingestion,
        trigger_ebay_sold_ingestion,
        trigger_ebay_listings_ingestion,
        trigger_leboncoin_listings_ingestion,
        trigger_leboncoin_sold_ingestion,
        trigger_vinted_listings_ingestion,
        trigger_full_ingestion,
        # Computation tasks
        scheduled_computation,
        trigger_product_computation,
        trigger_batch_computation,
        # LLM & Screenshot tasks
        validate_listing_with_llm,
        capture_listing_screenshot_task,
        process_opportunity_alerts,
        # Staleness & health tasks
        mark_stale_listings,
        check_system_health,
        # Audit tasks
        audit_ingestion_sample,
        run_on_demand_audit,
        # Enrichment tasks
        run_enrichment_batch,
    ]
    cron_jobs = [
        cron(ping, minute=0),  # Run ping every hour
        cron(scheduled_ebay_ingestion, hour=2),  # Run eBay ingestion daily at 2 AM
        cron(scheduled_leboncoin_ingestion, hour=3),  # Run LeBonCoin ingestion daily at 3 AM
        cron(scheduled_vinted_ingestion, hour=4),  # Run Vinted ingestion daily at 4 AM
        cron(scheduled_computation, hour=5),  # Run computation daily at 5 AM (after ingestion)
        cron(mark_stale_listings, hour=1),  # Mark stale listings daily at 1 AM
        cron(
            check_system_health,
            hour={0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22},
            minute=30,
        ),  # Check system health every 2 hours
        cron(run_enrichment_batch, hour=None, minute=30),  # Enrich listings every hour at :30
    ]

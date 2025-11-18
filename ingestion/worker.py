from typing import Any, Dict, List
from datetime import datetime, timezone
from arq import cron
from arq.connections import RedisSettings
from libs.common.settings import settings
from libs.common.log import logger
from libs.common.db import SessionLocal
from libs.common.models import ProductTemplate
from ingestion.ingestion import (
    run_full_ingestion,
    ingest_ebay_sold,
    ingest_ebay_listings,
    ingest_leboncoin_listings,
    ingest_leboncoin_sold,
    ingest_vinted_listings,
)
from ingestion.computation import (
    compute_pmn_for_product,
    compute_liquidity_score,
    compute_all_product_metrics,
)
from ingestion.alert_engine import trigger_alerts
from libs.common.llm_service import assess_listing_relevance
from libs.common.screenshot_service import capture_listing_screenshot
from libs.common.models import ListingObservation, ProductTemplate, MarketPriceNormal, ProductDailyMetrics

async def ping(ctx):
    logger.info("Worker alive.")


def _active_product_ids(provider: str | None = None) -> List[str]:
    with SessionLocal() as db:
        products = db.query(ProductTemplate).filter(ProductTemplate.is_active == True).all()

    product_ids: List[str] = []
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

    results: Dict[str, Dict[str, Any]] = {}
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

    return results

async def scheduled_leboncoin_ingestion(ctx):
    """Scheduled ingestion for all active products targeting LeBonCoin."""
    product_ids = _active_product_ids("leboncoin")
    logger.info(
        "Starting scheduled LeBonCoin ingestion for %d products", len(product_ids)
    )

    results: Dict[str, Dict[str, Any]] = {}
    for product_id in product_ids:
        try:
            result = await run_full_ingestion(
                product_id,
                {"leboncoin_listings": 20, "leboncoin_sold": 20},
                sources=["leboncoin"],
            )
            results[product_id] = result
            logger.info(
                f"Completed scheduled LeBonCoin ingestion for {product_id}: {result}"
            )
        except Exception as exc:
            logger.error(
                f"Error in scheduled LeBonCoin ingestion for {product_id}: {exc}"
            )
            results[product_id] = {"status": "error", "error": str(exc)}

    return results

async def scheduled_vinted_ingestion(ctx):
    """Scheduled ingestion for all active products targeting Vinted."""
    product_ids = _active_product_ids("vinted")
    logger.info("Starting scheduled Vinted ingestion for %d products", len(product_ids))

    results: Dict[str, Dict[str, Any]] = {}
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
            logger.info(f"Liquidity score for {product_id}: {liquidity_result.get('liquidity_score')}")
            
            return {
                "status": "success",
                "product_id": product_id,
                "pmn": pmn_result,
                "liquidity": liquidity_result
            }
            
    except Exception as exc:
        logger.error(f"Error in product computation for {product_id}: {exc}", exc_info=True)
        return {
            "status": "error",
            "product_id": product_id,
            "error": str(exc)
        }


async def trigger_batch_computation(ctx, product_ids: List[str] | None = None):
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
            listing = db.query(ListingObservation).filter(
                ListingObservation.obs_id == obs_id
            ).first()
            
            if not listing:
                return {"status": "error", "error": "Listing not found"}
            
            product_template = db.query(ProductTemplate).filter(
                ProductTemplate.product_id == listing.product_id
            ).first()
            
            if not product_template:
                return {"status": "error", "error": "Product template not found"}
            
            if not product_template.enable_llm_validation:
                return {"status": "skipped", "reason": "LLM validation not enabled for this product"}
            
            # Capture screenshot if URL available
            screenshot_path = None
            if listing.url:
                try:
                    screenshot_path = capture_listing_screenshot(
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
                observed_at=listing.observed_at or datetime.now(timezone.utc),
                is_sold=listing.is_sold or False,
                url=listing.url,
            )
            
            validation_result = assess_listing_relevance(
                listing_obj, screenshot_path, product_template, words_to_avoid
            )
            
            # Update listing with validation result
            listing.llm_validated = True
            listing.llm_validation_result = validation_result
            listing.llm_validated_at = datetime.now(timezone.utc)
            if screenshot_path:
                listing.screenshot_path = screenshot_path
            
            db.commit()
            
            logger.info(f"LLM validation completed for listing {obs_id}: {validation_result.get('is_relevant')}")
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
            listing = db.query(ListingObservation).filter(
                ListingObservation.obs_id == obs_id
            ).first()
            
            if not listing:
                return {"status": "error", "error": "Listing not found"}
            
            if not listing.url:
                return {"status": "error", "error": "Listing URL not available"}
            
            screenshot_path = capture_listing_screenshot(
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
            product_template = db.query(ProductTemplate).filter(
                ProductTemplate.product_id == product_id
            ).first()
            
            if not product_template:
                return {"status": "error", "error": "Product template not found"}
            
            pmn_data = db.query(MarketPriceNormal).filter(
                MarketPriceNormal.product_id == product_id
            ).first()
            
            if not pmn_data or not pmn_data.pmn:
                return {"status": "skipped", "reason": "PMN not computed for this product"}
            
            metrics = db.query(ProductDailyMetrics).filter(
                ProductDailyMetrics.product_id == product_id
            ).order_by(ProductDailyMetrics.date.desc()).first()
            
            # Get opportunities (listings below PMN)
            opportunities_list = db.query(ListingObservation).filter(
                ListingObservation.product_id == product_id,
                ListingObservation.is_sold == False,
                ListingObservation.price.isnot(None),
                ListingObservation.price < pmn_data.pmn,
            ).all()
            
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
            
            alert_events = trigger_alerts(opportunities, db)
            
            logger.info(f"Triggered {len(alert_events)} alerts for product {product_id}")
            return {
                "status": "success",
                "alerts_triggered": len(alert_events),
                "opportunities_found": len(opportunities_list),
            }
            
    except Exception as exc:
        logger.error(f"Error processing opportunity alerts for product {product_id}: {exc}", exc_info=True)
        return {"status": "error", "error": str(exc)}


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
    ]
    cron_jobs = [
        cron(ping, minute=0),  # Run ping every hour
        cron(scheduled_ebay_ingestion, hour=2),  # Run eBay ingestion daily at 2 AM
        cron(scheduled_leboncoin_ingestion, hour=3),  # Run LeBonCoin ingestion daily at 3 AM
        cron(scheduled_vinted_ingestion, hour=4),  # Run Vinted ingestion daily at 4 AM
        cron(scheduled_computation, hour=5),  # Run computation daily at 5 AM (after ingestion)
    ]

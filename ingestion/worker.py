from typing import Any, Dict, List
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
    compute_opportunity_score,
)
from libs.common.models import ProductTemplate, ListingObservation, MarketPriceNormal, ProductDailyMetrics
from libs.ai.gemini import analyze_listing
from libs.notifications.telegram import send_alert

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


async def perform_llm_analysis(ctx, obs_id: int):
    """
    Perform LLM analysis on a listing and send Telegram alert if verified.
    
    Workflow:
    1. Load listing and product data
    2. Download image if available
    3. Call Gemini LLM for analysis
    4. Update listing with LLM results
    5. Send Telegram alert if verified
    
    Args:
        ctx: ARQ context
        obs_id: Observation ID of the listing
        
    Returns:
        Dict with analysis results
    """
    logger.info(f"Starting LLM analysis for listing {obs_id}")
    
    try:
        with SessionLocal() as db:
            # Load listing with product
            listing = (
                db.query(ListingObservation)
                .join(ProductTemplate)
                .filter(ListingObservation.obs_id == obs_id)
                .first()
            )
            
            if not listing:
                logger.error(f"Listing {obs_id} not found")
                return {"status": "error", "error": "listing_not_found"}
            
            product = listing.product
            
            # Check if LLM verification is enabled for this product
            if not product.llm_verification_enabled:
                logger.info(f"LLM verification disabled for product {product.product_id}")
                return {"status": "skipped", "reason": "llm_disabled"}
            
            # Get PMN data for margin calculation
            pmn_data = db.query(MarketPriceNormal).filter(
                MarketPriceNormal.product_id == product.product_id
            ).first()
            
            if not pmn_data or not pmn_data.pmn:
                logger.warning(f"No PMN data for product {product.product_id}")
                return {"status": "error", "error": "no_pmn_data"}
            
            # Calculate opportunity score first
            product_metrics = db.query(ProductDailyMetrics).filter(
                ProductDailyMetrics.product_id == product.product_id
            ).order_by(ProductDailyMetrics.date.desc()).first()
            
            opportunity = compute_opportunity_score(
                listing,
                product_metrics,
                pmn_data,
                product
            )
            
            # Only proceed with LLM if opportunity score is above threshold
            opportunity_score = opportunity.get("opportunity_score", 0)
            if opportunity_score < 40:  # Threshold for LLM analysis
                logger.info(f"Listing {obs_id} opportunity score {opportunity_score} below threshold")
                return {"status": "skipped", "reason": "low_opportunity_score", "score": opportunity_score}
            
            # Perform LLM analysis
            analysis_result = analyze_listing(
                image_url=listing.url,  # Use listing URL as image source (may need adjustment)
                title=listing.title or "",
                price=float(listing.price) if listing.price else 0.0,
                description=None,  # Description not stored in ListingObservation
                target_description=product.target_description or "",
                negative_keywords=product.negative_keywords
            )
            
            # Update listing with LLM results
            listing.llm_score = analysis_result.get("score")
            listing.llm_reasoning = analysis_result.get("reasoning")
            listing.llm_verified = analysis_result.get("verified", False)
            
            db.commit()
            
            # Send Telegram alert if verified
            if analysis_result.get("verified", False):
                listing_dict = {
                    "obs_id": listing.obs_id,
                    "title": listing.title,
                    "price": float(listing.price) if listing.price else 0.0,
                    "source": listing.source,
                    "condition": listing.condition,
                    "url": listing.url
                }
                
                product_dict = {
                    "name": product.name,
                    "target_description": product.target_description
                }
                
                telegram_sent = send_alert(listing_dict, product_dict, analysis_result)
                
                logger.info(
                    f"LLM analysis completed for listing {obs_id}: "
                    f"score={analysis_result.get('score')}, verified={analysis_result.get('verified')}, "
                    f"telegram_sent={telegram_sent}"
                )
                
                return {
                    "status": "success",
                    "obs_id": obs_id,
                    "score": analysis_result.get("score"),
                    "verified": analysis_result.get("verified"),
                    "telegram_sent": telegram_sent
                }
            else:
                logger.info(f"Listing {obs_id} not verified by LLM (score: {analysis_result.get('score')})")
                return {
                    "status": "success",
                    "obs_id": obs_id,
                    "score": analysis_result.get("score"),
                    "verified": False
                }
                
    except Exception as exc:
        logger.error(f"Error in LLM analysis for listing {obs_id}: {exc}", exc_info=True)
        return {"status": "error", "error": str(exc)}


async def process_listing_for_llm(ctx, product_id: str, obs_id: int):
    """
    Process a listing through the full pipeline:
    1. Compute metrics and opportunity score
    2. If score > threshold AND LLM enabled: enqueue LLM analysis
    
    This is called after a listing is ingested and metrics are computed.
    """
    logger.info(f"Processing listing {obs_id} for product {product_id} through LLM pipeline")
    
    try:
        with SessionLocal() as db:
            product = db.query(ProductTemplate).filter(
                ProductTemplate.product_id == product_id
            ).first()
            
            if not product or not product.llm_verification_enabled:
                return {"status": "skipped", "reason": "llm_disabled"}
            
            listing = db.query(ListingObservation).filter(
                ListingObservation.obs_id == obs_id
            ).first()
            
            if not listing:
                return {"status": "error", "error": "listing_not_found"}
            
            # Get PMN and metrics
            pmn_data = db.query(MarketPriceNormal).filter(
                MarketPriceNormal.product_id == product_id
            ).first()
            
            product_metrics = db.query(ProductDailyMetrics).filter(
                ProductDailyMetrics.product_id == product_id
            ).order_by(ProductDailyMetrics.date.desc()).first()
            
            # Compute opportunity score
            opportunity = compute_opportunity_score(
                listing,
                product_metrics,
                pmn_data,
                product
            )
            
            opportunity_score = opportunity.get("opportunity_score", 0)
            
            # If score is high enough, enqueue LLM analysis
            if opportunity_score >= 40:  # Threshold
                # Call LLM analysis directly (it's async and we're in async context)
                # For better job tracking, this could be enqueued as a separate job
                llm_result = await perform_llm_analysis(ctx, obs_id)
                logger.info(f"LLM analysis result for listing {obs_id}: {llm_result.get('status')}")
                return {
                    "status": "processed",
                    "obs_id": obs_id,
                    "opportunity_score": opportunity_score,
                    "llm_result": llm_result
                }
            else:
                return {
                    "status": "skipped",
                    "reason": "low_opportunity_score",
                    "opportunity_score": opportunity_score
                }
                
    except Exception as exc:
        logger.error(f"Error processing listing {obs_id} for LLM: {exc}", exc_info=True)
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
        # LLM tasks
        perform_llm_analysis,
        process_listing_for_llm,
    ]
    cron_jobs = [
        cron(ping, minute=0),  # Run ping every hour
        cron(scheduled_ebay_ingestion, hour=2),  # Run eBay ingestion daily at 2 AM
        cron(scheduled_leboncoin_ingestion, hour=3),  # Run LeBonCoin ingestion daily at 3 AM
        cron(scheduled_vinted_ingestion, hour=4),  # Run Vinted ingestion daily at 4 AM
        cron(scheduled_computation, hour=5),  # Run computation daily at 5 AM (after ingestion)
    ]

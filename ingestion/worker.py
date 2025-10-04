from typing import List
from arq import cron
from arq.connections import RedisSettings
from libs.common.settings import settings
from libs.common.log import logger
from ingestion.ingestion import run_full_ingestion, ingest_ebay_sold, ingest_ebay_listings, ingest_leboncoin_listings, ingest_leboncoin_sold, ingest_vinted_listings, ingest_vinted_sold

async def ping(ctx):
    logger.info("Worker alive.")

async def scheduled_ebay_ingestion(ctx):
    """Scheduled ingestion for predefined keywords from eBay"""
    keywords = [
        "iPhone",
        "Sony headphones",
        "Nintendo Switch",
        "MacBook",
        "Samsung Galaxy"
    ]

    logger.info("Starting scheduled eBay ingestion")

    results = {}
    for keyword in keywords:
        try:
            result = await run_full_ingestion(keyword, {"ebay_sold": 20, "ebay_listings": 20}, sources=["ebay"])
            results[keyword] = result
            logger.info(f"Completed eBay ingestion for {keyword}: {result}")
        except Exception as e:
            logger.error(f"Error in scheduled eBay ingestion for {keyword}: {e}")
            results[keyword] = {"status": "error", "error": str(e)}

    return results

async def scheduled_leboncoin_ingestion(ctx):
    """Scheduled ingestion for predefined keywords from LeBonCoin"""
    keywords = [
        "iPhone",
        "Sony casque",
        "Nintendo Switch",
        "MacBook",
        "Samsung Galaxy"
    ]

    logger.info("Starting scheduled LeBonCoin ingestion")

    results = {}
    for keyword in keywords:
        try:
            result = await run_full_ingestion(keyword, {"leboncoin_listings": 20}, sources=["leboncoin"])
            results[keyword] = result
            logger.info(f"Completed LeBonCoin ingestion for {keyword}: {result}")
        except Exception as e:
            logger.error(f"Error in scheduled LeBonCoin ingestion for {keyword}: {e}")
            results[keyword] = {"status": "error", "error": str(e)}

    return results

async def scheduled_vinted_ingestion(ctx):
    """Scheduled ingestion for predefined keywords from Vinted"""
    keywords = [
        "Nike",
        "Adidas",
        "Levi's",
        "H&M",
        "Zara"
    ]

    logger.info("Starting scheduled Vinted ingestion")

    results = {}
    for keyword in keywords:
        try:
            result = await run_full_ingestion(keyword, {"vinted_listings": 20}, sources=["vinted"])
            results[keyword] = result
            logger.info(f"Completed Vinted ingestion for {keyword}: {result}")
        except Exception as e:
            logger.error(f"Error in scheduled Vinted ingestion for {keyword}: {e}")
            results[keyword] = {"status": "error", "error": str(e)}

    return results

async def trigger_ebay_sold_ingestion(ctx, keyword: str, limit: int = 50):
    """Trigger eBay sold items ingestion for a specific keyword"""
    logger.info(f"Triggering eBay sold items ingestion for: {keyword}")
    result = await ingest_ebay_sold(keyword, limit)
    logger.info(f"Completed sold items ingestion for {keyword}: {result}")
    return result

async def trigger_ebay_listings_ingestion(ctx, keyword: str, limit: int = 50):
    """Trigger eBay listings ingestion for a specific keyword"""
    logger.info(f"Triggering eBay listings ingestion for: {keyword}")
    result = await ingest_ebay_listings(keyword, limit)
    logger.info(f"Completed listings ingestion for {keyword}: {result}")
    return result

async def trigger_full_ingestion(ctx, keyword: str, sold_limit: int = 50, listings_limit: int = 50, sources = None):
    """Trigger full ingestion pipeline for a specific keyword"""
    if sources is None:
        sources = ["ebay", "leboncoin"]
    logger.info(f"Triggering full ingestion for: {keyword} from sources: {sources}")
    result = await run_full_ingestion(keyword, {"ebay_sold": sold_limit, "ebay_listings": listings_limit, "leboncoin_listings": listings_limit}, sources)
    logger.info(f"Completed full ingestion for {keyword}: {result}")
    return result

async def trigger_leboncoin_listings_ingestion(ctx, keyword: str, limit: int = 50):
    """Trigger LeBonCoin listings ingestion for a specific keyword"""
    logger.info(f"Triggering LeBonCoin listings ingestion for: {keyword}")
    result = await ingest_leboncoin_listings(keyword, limit)
    logger.info(f"Completed LeBonCoin listings ingestion for {keyword}: {result}")
    return result

async def trigger_leboncoin_sold_ingestion(ctx, keyword: str, limit: int = 50):
    """Trigger LeBonCoin 'sold' items ingestion for a specific keyword"""
    logger.info(f"Triggering LeBonCoin 'sold' items ingestion for: {keyword}")
    result = await ingest_leboncoin_sold(keyword, limit)
    logger.info(f"Completed LeBonCoin 'sold' items ingestion for {keyword}: {result}")
    return result

async def trigger_vinted_listings_ingestion(ctx, keyword: str, limit: int = 50):
    """Trigger Vinted listings ingestion for a specific keyword"""
    logger.info(f"Triggering Vinted listings ingestion for: {keyword}")
    result = await ingest_vinted_listings(keyword, limit)
    logger.info(f"Completed Vinted listings ingestion for {keyword}: {result}")
    return result

async def trigger_vinted_sold_ingestion(ctx, keyword: str, limit: int = 50):
    """Trigger Vinted 'sold' items ingestion for a specific keyword"""
    logger.info(f"Triggering Vinted 'sold' items ingestion for: {keyword}")
    result = await ingest_vinted_sold(keyword, limit)
    logger.info(f"Completed Vinted 'sold' items ingestion for {keyword}: {result}")
    return result

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
        trigger_vinted_sold_ingestion,
        trigger_full_ingestion
    ]
    cron_jobs = [
        cron(ping, minute=0),  # Run ping every hour
        cron(scheduled_ebay_ingestion, hour=2),  # Run eBay ingestion daily at 2 AM
        cron(scheduled_leboncoin_ingestion, hour=3),  # Run LeBonCoin ingestion daily at 3 AM
        cron(scheduled_vinted_ingestion, hour=4),  # Run Vinted ingestion daily at 4 AM
    ]

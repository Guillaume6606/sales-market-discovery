from fastapi import FastAPI, Depends, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from libs.common.db import get_db, engine
from libs.common.models import Base
from libs.common.log import logger
from libs.common.settings import settings
import asyncio
from typing import Optional

# ARQ imports for proper job enqueuing
from arq import create_pool
from arq.connections import RedisSettings
import json
import uuid
from datetime import datetime

# ARQ-based ingestion - no need to import heavy ingestion modules in backend

# Create database tables (with error handling)
try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully")
except Exception as e:
    logger.warning(f"Could not create database tables: {e}")
    logger.info("Tables may already exist or database connection may be unavailable")

# Global ARQ pool
arq_pool = None

app = FastAPI(title="Market Discovery API", version="0.1.0")

@app.on_event("startup")
async def startup_event():
    """Initialize ARQ pool on startup"""
    global arq_pool
    try:
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        arq_pool = await create_pool(redis_settings)
        # Test the connection
        await arq_pool.ping()
        logger.info("ARQ pool connected successfully")
    except Exception as e:
        logger.error(f"Failed to connect to ARQ pool: {e}")
        arq_pool = None

@app.on_event("shutdown")
async def shutdown_event():
    """Close ARQ pool on shutdown"""
    global arq_pool
    if arq_pool:
        arq_pool.close()
        await arq_pool.wait_closed()
        logger.info("ARQ pool closed")

async def enqueue_arq_job(function_name: str, *args, **kwargs):
    """Enqueue a job using ARQ pool"""
    if not arq_pool:
        raise Exception("ARQ pool not available")
    
    # Use ARQ's native enqueue_job method
    job = await arq_pool.enqueue_job(function_name, *args, **kwargs)
    return job

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DiscoveryItem(BaseModel):
    product_id: str
    title: str
    brand: str | None = None
    pmn: float | None = None
    price_min_market: float | None = None
    delta_vs_pmn_pct: float | None = None
    liquidity_score: float | None = None
    trend_score: float | None = None

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/products/discovery")
def products_discovery(
    category: str | None = None,
    brand: str | None = None,
    min_margin: float | None = Query(None, description="min % below PMN"),
    limit: int = 50,
    db: Session = Depends(get_db),
):
    # Placeholder: return mock data for now
    logger.info("Discovery query: category=%s brand=%s", category, brand)
    items = [
        DiscoveryItem(
            product_id="00000000-0000-0000-0000-000000000001",
            title="Sony WH-1000XM4 Headphones",
            brand="Sony",
            pmn=180.0,
            price_min_market=140.0,
            delta_vs_pmn_pct=-22.2,
            liquidity_score=0.9,
            trend_score=1.2,
        )
    ]
    return {"items": [i.model_dump() for i in items][:limit]}

class ProductDetail(BaseModel):
    product_id: str
    title: str
    brand: str | None = None
    pmn: float | None = None
    pmn_low: float | None = None
    pmn_high: float | None = None
    recent_solds: list[dict]
    live_listings: list[dict]

@app.get("/products/{product_id}")
def product_detail(product_id: str, db: Session = Depends(get_db)):
    # Placeholder with mock data
    return ProductDetail(
        product_id=product_id,
        title="Sony WH-1000XM4 Headphones",
        brand="Sony",
        pmn=180.0,
        pmn_low=170.0,
        pmn_high=190.0,
        recent_solds=[{"price": 175, "date": "2025-09-15", "condition": "good"}],
        live_listings=[{"price": 149, "seller": "john_doe", "link": "https://example.com"}],
    ).model_dump()

# Ingestion endpoints
@app.post("/ingestion/trigger")
async def trigger_ingestion(
    keyword: str,
    sold_limit: int = 50,
    listings_limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger full ingestion pipeline for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing full ingestion for keyword: {keyword}")
        job = await enqueue_arq_job(
            'trigger_full_ingestion',
            keyword,
            sold_limit,
            listings_limit,
            sources=["ebay", "leboncoin"]
        )
        return {
            "message": f"Ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue ingestion job: {e}")
        return {"error": "Failed to enqueue job", "message": str(e)}

@app.post("/ingestion/trigger-sold")
async def trigger_sold_ingestion(
    keyword: str,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger eBay sold items ingestion for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing sold items ingestion for keyword: {keyword}")
        job = await enqueue_arq_job('trigger_ebay_sold_ingestion', keyword, limit)
        return {
            "message": f"Sold items ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue sold items job: {e}")
        return {"error": "Failed to enqueue job", "message": str(e)}

@app.post("/ingestion/trigger-listings")
async def trigger_listings_ingestion(
    keyword: str,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger eBay listings ingestion for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing listings ingestion for keyword: {keyword}")
        job = await enqueue_arq_job('trigger_ebay_listings_ingestion', keyword, limit)
        return {
            "message": f"Listings ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue listings job: {e}")
        return {"error": "Failed to enqueue job", "message": str(e)}

def normalize_condition(condition_raw: str) -> str | None:
    """Normalize condition to standard categories"""
    if not condition_raw:
        return None

    condition_lower = condition_raw.lower()

    # Common condition mappings
    if any(word in condition_lower for word in ["new", "brand new", "nib", "neuf", "nouveau"]):
        return "new"
    elif any(word in condition_lower for word in ["like new", "excellent", "mint", "comme neuf"]):
        return "like_new"
    elif any(word in condition_lower for word in ["very good", "good", "bien", "bon état"]):
        return "good"
    elif any(word in condition_lower for word in ["acceptable", "fair", "poor", "satisfaisant"]):
        return "fair"

    return None

@app.get("/listings")
def get_listings(
    source: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    condition: str | None = None,
    include_sold: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get filtered listings from the database"""
    from libs.common.models import ListingObservation, ProductRef

    query = db.query(ListingObservation).join(ProductRef)

    # Apply filters
    if source:
        query = query.filter(ListingObservation.source == source)

    if category:
        query = query.filter(ProductRef.category.ilike(f"%{category}%"))

    if brand:
        query = query.filter(ProductRef.brand.ilike(f"%{brand}%"))

    if price_min is not None:
        query = query.filter(ListingObservation.price >= price_min)

    if price_max is not None:
        query = query.filter(ListingObservation.price <= price_max)

    if condition:
        query = query.filter(ListingObservation.condition.ilike(f"%{condition}%"))

    if not include_sold:
        query = query.filter(ListingObservation.is_sold == False)

    # Get results
    listings = query.limit(limit).all()

    # Convert to response format
    result = []
    for listing in listings:
        result.append({
            "listing_id": listing.listing_id,
            "title": listing.title,
            "price": float(listing.price) if listing.price else None,
            "currency": listing.currency,
            "condition_raw": listing.condition,
            "condition_norm": normalize_condition(listing.condition),
            "location": listing.location,
            "seller_rating": float(listing.seller_rating) if listing.seller_rating else None,
            "shipping_cost": float(listing.shipping_cost) if listing.shipping_cost else None,
            "observed_at": listing.observed_at.isoformat() if listing.observed_at else None,
            "is_sold": listing.is_sold,
            "source": listing.source,
            "url": None  # Not stored in database currently
        })

    return {"listings": result}

@app.get("/ingestion/status")
async def ingestion_status(db: Session = Depends(get_db)):
    """Get current ingestion status and statistics"""
    from libs.common.models import ProductRef, ListingObservation

    # Get basic statistics
    total_products = db.query(ProductRef).count()
    total_observations = db.query(ListingObservation).count()
    sold_observations = db.query(ListingObservation).filter(ListingObservation.is_sold == True).count()
    active_listings = db.query(ListingObservation).filter(ListingObservation.is_sold == False).count()

    # Get source-specific statistics
    ebay_observations = db.query(ListingObservation).filter(ListingObservation.source == "ebay").count()
    leboncoin_observations = db.query(ListingObservation).filter(ListingObservation.source == "leboncoin").count()
    vinted_observations = db.query(ListingObservation).filter(ListingObservation.source == "vinted").count()

    # Add ARQ queue status
    arq_status = "disconnected"
    queue_length = 0
    if arq_pool:
        try:
            # Use ARQ pool to get queue length
            queue_length = await arq_pool.zcard('arq:queue')
            arq_status = "connected"
        except:
            arq_status = "error"
    
    return {
        "total_products": total_products,
        "total_observations": total_observations,
        "sold_observations": sold_observations,
        "active_listings": active_listings,
        "ebay_observations": ebay_observations,
        "leboncoin_observations": leboncoin_observations,
        "vinted_observations": vinted_observations,
        "arq_status": arq_status,
        "queue_length": queue_length,
        "last_updated": "Real-time"
    }

# LeBonCoin-specific endpoints
@app.post("/ingestion/leboncoin/trigger")
async def trigger_leboncoin_ingestion(
    keyword: str,
    listings_limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger LeBonCoin ingestion pipeline for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing LeBonCoin ingestion for keyword: {keyword}")
        job = await enqueue_arq_job(
            'trigger_full_ingestion',
            keyword,
            listings_limit,
            listings_limit,
            sources=["leboncoin"]
        )
        logger.info(f"Successfully enqueued job with ID: {job.job_id}")
        return {
            "message": f"LeBonCoin ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue LeBonCoin job: {e}", exc_info=True)
        return {"error": "Failed to enqueue job", "message": str(e)}

@app.post("/ingestion/leboncoin/trigger-listings")
async def trigger_leboncoin_listings_ingestion_endpoint(
    keyword: str,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger LeBonCoin listings ingestion for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing LeBonCoin listings ingestion for keyword: {keyword}")
        job = await enqueue_arq_job('trigger_leboncoin_listings_ingestion', keyword, limit)
        return {
            "message": f"LeBonCoin listings ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue LeBonCoin listings job: {e}")
        return {"error": "Failed to enqueue job", "message": str(e)}

@app.post("/ingestion/leboncoin/trigger-sold")
async def trigger_leboncoin_sold_ingestion_endpoint(
    keyword: str,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger LeBonCoin 'sold' items ingestion for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing LeBonCoin 'sold' items ingestion for keyword: {keyword}")
        job = await enqueue_arq_job('trigger_leboncoin_sold_ingestion', keyword, limit)
        return {
            "message": f"LeBonCoin 'sold' items ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue LeBonCoin sold job: {e}")
        return {"error": "Failed to enqueue job", "message": str(e)}

# Vinted-specific endpoints
@app.post("/ingestion/vinted/trigger")
async def trigger_vinted_ingestion(
    keyword: str,
    listings_limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger Vinted ingestion pipeline for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing Vinted ingestion for keyword: {keyword}")
        job = await enqueue_arq_job(
            'trigger_full_ingestion',
            keyword,
            listings_limit,
            listings_limit,
            sources=["vinted"]
        )
        return {
            "message": f"Vinted ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue Vinted job: {e}")
        return {"error": "Failed to enqueue job", "message": str(e)}

@app.post("/ingestion/vinted/trigger-listings")
async def trigger_vinted_listings_ingestion_endpoint(
    keyword: str,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger Vinted listings ingestion for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing Vinted listings ingestion for keyword: {keyword}")
        job = await enqueue_arq_job('trigger_vinted_listings_ingestion', keyword, limit)
        return {
            "message": f"Vinted listings ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue Vinted listings job: {e}")
        return {"error": "Failed to enqueue job", "message": str(e)}

@app.post("/ingestion/vinted/trigger-sold")
async def trigger_vinted_sold_ingestion_endpoint(
    keyword: str,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Trigger Vinted 'sold' items ingestion for a keyword"""
    if not arq_pool:
        return {"error": "ARQ pool not available", "message": "Please ensure Redis and ingestion service are running"}
    
    try:
        logger.info(f"Enqueueing Vinted 'sold' items ingestion for keyword: {keyword}")
        job = await enqueue_arq_job('trigger_vinted_sold_ingestion', keyword, limit)
        return {
            "message": f"Vinted 'sold' items ingestion job enqueued for keyword: {keyword}", 
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as e:
        logger.error(f"Failed to enqueue Vinted sold job: {e}")
        return {"error": "Failed to enqueue job", "message": str(e)}

# ARQ monitoring endpoints
@app.get("/ingestion/queue/status")
async def get_queue_status():
    """Get ARQ queue status and statistics"""
    if not arq_pool:
        return {"error": "ARQ pool not available"}
    
    try:
        queued = await arq_pool.zcard('arq:queue')
        return {
            "arq_connected": True,
            "queued_jobs": queued,
            "redis_url": settings.redis_url.replace(settings.redis_url.split('@')[0].split('//')[1] + '@', '***@') if '@' in settings.redis_url else settings.redis_url
        }
    except Exception as e:
        return {"arq_connected": False, "error": str(e)}

@app.get("/ingestion/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get status of a specific ARQ job"""
    if not arq_pool:
        return {"error": "ARQ pool not available"}
    
    try:
        # For job results, we'd need to implement custom logic since pool doesn't have direct result method
        job_result = await arq_pool.get(f'arq:result:{job_id}')
        return {
            "job_id": job_id,
            "result": job_result
        }
    except Exception as e:
        return {"job_id": job_id, "error": str(e)}

@app.post("/ingestion/test-connection")
async def test_arq_connection():
    """Test ARQ connection and enqueue a simple ping job"""
    if not arq_pool:
        return {"error": "ARQ pool not available"}
    
    try:
        # Test basic Redis connection
        await arq_pool.ping()
        
        # Try to enqueue a simple ping job
        job = await enqueue_arq_job('ping')
        
        return {
            "arq_connected": True,
            "ping_job_id": job.job_id,
            "message": "ARQ connection successful and test job enqueued"
        }
    except Exception as e:
        logger.error(f"ARQ connection test failed: {e}")
        return {"arq_connected": False, "error": str(e)}


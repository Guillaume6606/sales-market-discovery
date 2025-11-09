from fastapi import FastAPI, Depends, Query, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc
from libs.common.db import get_db, engine
from libs.common.models import Base, Category, ProductTemplate, ListingObservation, MarketPriceNormal, ProductDailyMetrics
from libs.common.log import logger
from libs.common.settings import settings, SUPPORTED_PROVIDERS
from typing import Any, Dict, List

# ARQ imports for proper job enqueuing
from arq import create_pool
from arq.connections import RedisSettings
from datetime import datetime, timedelta, timezone

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
    min_margin: float | None = Query(None, description="min % below PMN (e.g., -20 for 20% below)"),
    max_margin: float | None = Query(None, description="max % below PMN (e.g., 0)"),
    min_liquidity: float | None = Query(None, description="min liquidity score (0-1)"),
    min_trend: float | None = Query(None, description="min trend score"),
    sort_by: str | None = Query("margin", description="Sort by: margin, liquidity, trend, last_sold"),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    Discover arbitrage opportunities by comparing market prices to PMN.
    Returns products with active listings below their reference price.
    """
    logger.info(f"Discovery query: category={category}, brand={brand}, min_margin={min_margin}")
    
    # Base query joining all necessary tables
    query = (
        db.query(
            ProductTemplate,
            MarketPriceNormal,
            ProductDailyMetrics,
            func.min(ListingObservation.price).label("best_price"),
            func.count(ListingObservation.obs_id).label("listing_count"),
        )
        .outerjoin(MarketPriceNormal, ProductTemplate.product_id == MarketPriceNormal.product_id)
        .outerjoin(
            ProductDailyMetrics,
            and_(
                ProductTemplate.product_id == ProductDailyMetrics.product_id,
                ProductDailyMetrics.date == func.current_date(),
            ),
        )
        .outerjoin(
            ListingObservation,
            and_(
                ProductTemplate.product_id == ListingObservation.product_id,
                ListingObservation.is_sold == False,
                ListingObservation.price.isnot(None),
            ),
        )
        .filter(ProductTemplate.is_active == True)
        .group_by(
            ProductTemplate.product_id,
            MarketPriceNormal.product_id,
            ProductDailyMetrics.product_id,
            ProductDailyMetrics.date,
        )
    )
    
    # Apply filters
    if category:
        query = query.join(Category).filter(Category.name.ilike(f"%{category}%"))
    
    if brand:
        query = query.filter(ProductTemplate.brand.ilike(f"%{brand}%"))
    
    # Execute query
    results = query.all()
    
    # Process results and calculate margins
    items = []
    for product, pmn, metrics, best_price, listing_count in results:
        # Skip if no PMN calculated
        if not pmn or pmn.pmn is None:
            continue
            
        # Calculate delta vs PMN
        pmn_value = _decimal_to_float(pmn.pmn)
        best_price_value = _decimal_to_float(best_price)
        
        # Skip if no active listings
        if best_price_value is None or listing_count == 0:
            continue
            
        delta_pct = ((best_price_value - pmn_value) / pmn_value * 100) if pmn_value else None
        
        # Apply margin filters
        if min_margin is not None and (delta_pct is None or delta_pct > min_margin):
            continue
        if max_margin is not None and (delta_pct is None or delta_pct < max_margin):
            continue
            
        # Get metrics if available
        liquidity = _decimal_to_float(metrics.liquidity_score) if metrics else None
        trend = _decimal_to_float(metrics.trend_score) if metrics else None
        
        # Apply metric filters
        if min_liquidity is not None and (liquidity is None or liquidity < min_liquidity):
            continue
        if min_trend is not None and (trend is None or trend < min_trend):
            continue
        
        items.append(
            DiscoveryItem(
                product_id=str(product.product_id),
                title=product.name,
                brand=product.brand,
                pmn=pmn_value,
                price_min_market=best_price_value,
                delta_vs_pmn_pct=round(delta_pct, 2) if delta_pct else None,
                liquidity_score=liquidity,
                trend_score=trend,
            )
        )
    
    # Sort results
    if sort_by == "margin":
        items.sort(key=lambda x: x.delta_vs_pmn_pct if x.delta_vs_pmn_pct else 0)
    elif sort_by == "liquidity":
        items.sort(key=lambda x: x.liquidity_score if x.liquidity_score else 0, reverse=True)
    elif sort_by == "trend":
        items.sort(key=lambda x: x.trend_score if x.trend_score else 0, reverse=True)
    
    # Apply pagination
    paginated_items = items[offset : offset + limit]
    
    return {
        "items": [i.model_dump() for i in paginated_items],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }

class ProductDetail(BaseModel):
    product_id: str
    title: str
    brand: str | None = None
    category: str | None = None
    description: str | None = None
    pmn: float | None = None
    pmn_low: float | None = None
    pmn_high: float | None = None
    price_median_30d: float | None = None
    price_p25: float | None = None
    price_p75: float | None = None
    sold_count_30d: int | None = None
    liquidity_score: float | None = None
    trend_score: float | None = None
    recent_solds: list[dict]
    live_listings: list[dict]
    providers: list[str] = []


class CategoryCreate(BaseModel):
    name: str
    description: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProductTemplateCreate(BaseModel):
    name: str
    description: str | None = None
    search_query: str
    category_id: str
    brand: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    providers: List[str] | None = None
    is_active: bool = True


class ProductTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    search_query: str | None = None
    category_id: str | None = None
    brand: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    providers: List[str] | None = None
    is_active: bool | None = None


def _decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _serialize_category(category: Category | None) -> Dict[str, Any] | None:
    if not category:
        return None
    return {
        "category_id": str(category.category_id),
        "name": category.name,
        "description": category.description,
        "created_at": category.created_at.isoformat() if category.created_at else None,
        "updated_at": category.updated_at.isoformat() if category.updated_at else None,
    }


def _serialize_product_template(product: ProductTemplate) -> Dict[str, Any]:
    return {
        "product_id": str(product.product_id),
        "name": product.name,
        "description": product.description,
        "search_query": product.search_query,
        "brand": product.brand,
        "price_min": _decimal_to_float(product.price_min),
        "price_max": _decimal_to_float(product.price_max),
        "providers": list(product.providers or []),
        "is_active": product.is_active,
        "category": _serialize_category(product.category),
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
        "last_ingested_at": product.last_ingested_at.isoformat() if product.last_ingested_at else None,
    }


def _get_product_or_404(db: Session, product_id: str) -> ProductTemplate:
    product = (
        db.query(ProductTemplate)
        .filter(ProductTemplate.product_id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


def _ensure_provider_allowed(product: ProductTemplate, provider: str) -> None:
    if product.providers and provider not in product.providers:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Product is not configured for provider '{provider}'",
        )

@app.get("/products/{product_id}")
def product_detail(product_id: str, db: Session = Depends(get_db)):
    """
    Get detailed information for a specific product including:
    - PMN and price statistics
    - Recent sold listings (last 30 days)
    - Active market listings
    - Liquidity and trend metrics
    """
    # Get product with related data
    product = (
        db.query(ProductTemplate)
        .filter(ProductTemplate.product_id == product_id)
        .first()
    )
    
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    
    # Get PMN data
    pmn_data = (
        db.query(MarketPriceNormal)
        .filter(MarketPriceNormal.product_id == product_id)
        .first()
    )
    
    # Get latest metrics
    metrics = (
        db.query(ProductDailyMetrics)
        .filter(ProductDailyMetrics.product_id == product_id)
        .order_by(desc(ProductDailyMetrics.date))
        .first()
    )
    
    # Get recent sold listings (last 30 days)
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent_solds_query = (
        db.query(ListingObservation)
        .filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold == True,
            ListingObservation.observed_at >= thirty_days_ago,
        )
        .order_by(desc(ListingObservation.observed_at))
        .limit(20)
    )
    
    recent_solds = [
        {
            "obs_id": obs.obs_id,
            "title": obs.title,
            "description": obs.description,
            "price": _decimal_to_float(obs.price),
            "currency": obs.currency,
            "condition": obs.condition,
            "location": obs.location,
            "seller_rating": _decimal_to_float(obs.seller_rating),
            "source": obs.source,
            "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
        }
        for obs in recent_solds_query.all()
    ]
    
    # Get active listings
    live_listings_query = (
        db.query(ListingObservation)
        .filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold == False,
        )
        .order_by(ListingObservation.price)
        .limit(20)
    )
    
    live_listings = [
        {
            "obs_id": obs.obs_id,
            "title": obs.title,
            "description": obs.description,
            "price": _decimal_to_float(obs.price),
            "currency": obs.currency,
            "condition": obs.condition,
            "location": obs.location,
            "seller_rating": _decimal_to_float(obs.seller_rating),
            "shipping_cost": _decimal_to_float(obs.shipping_cost),
            "source": obs.source,
            "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
        }
        for obs in live_listings_query.all()
    ]
    
    # Build response
    return ProductDetail(
        product_id=str(product.product_id),
        title=product.name,
        brand=product.brand,
        category=product.category.name if product.category else None,
        description=product.description,
        pmn=_decimal_to_float(pmn_data.pmn) if pmn_data else None,
        pmn_low=_decimal_to_float(pmn_data.pmn_low) if pmn_data else None,
        pmn_high=_decimal_to_float(pmn_data.pmn_high) if pmn_data else None,
        price_median_30d=_decimal_to_float(metrics.price_median) if metrics else None,
        price_p25=_decimal_to_float(metrics.price_p25) if metrics else None,
        price_p75=_decimal_to_float(metrics.price_p75) if metrics else None,
        sold_count_30d=metrics.sold_count_30d if metrics else None,
        liquidity_score=_decimal_to_float(metrics.liquidity_score) if metrics else None,
        trend_score=_decimal_to_float(metrics.trend_score) if metrics else None,
        recent_solds=recent_solds,
        live_listings=live_listings,
        providers=list(product.providers or []),
    ).model_dump()


@app.get("/products/{product_id}/price-history")
def product_price_history(
    product_id: str,
    days: int = Query(30, description="Number of days of history (7, 30, or 90)"),
    db: Session = Depends(get_db),
):
    """
    Get price history for a product over time.
    Returns time-series data for sold items and active listings.
    """
    # Validate product exists
    product = (
        db.query(ProductTemplate)
        .filter(ProductTemplate.product_id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    
    # Get date range
    days = min(days, 90)  # Cap at 90 days
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    # Get sold items grouped by day
    sold_history = (
        db.query(
            func.date(ListingObservation.observed_at).label("date"),
            func.avg(ListingObservation.price).label("avg_price"),
            func.min(ListingObservation.price).label("min_price"),
            func.max(ListingObservation.price).label("max_price"),
            func.count(ListingObservation.obs_id).label("count"),
        )
        .filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold == True,
            ListingObservation.observed_at >= start_date,
            ListingObservation.price.isnot(None),
        )
        .group_by(func.date(ListingObservation.observed_at))
        .order_by(func.date(ListingObservation.observed_at))
        .all()
    )
    
    # Get current active listings grouped by day first observed
    active_history = (
        db.query(
            func.date(ListingObservation.observed_at).label("date"),
            func.avg(ListingObservation.price).label("avg_price"),
            func.min(ListingObservation.price).label("min_price"),
            func.max(ListingObservation.price).label("max_price"),
            func.count(ListingObservation.obs_id).label("count"),
        )
        .filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold == False,
            ListingObservation.observed_at >= start_date,
            ListingObservation.price.isnot(None),
        )
        .group_by(func.date(ListingObservation.observed_at))
        .order_by(func.date(ListingObservation.observed_at))
        .all()
    )
    
    # Get PMN for reference line
    pmn_data = (
        db.query(MarketPriceNormal)
        .filter(MarketPriceNormal.product_id == product_id)
        .first()
    )
    
    return {
        "product_id": str(product_id),
        "days": days,
        "pmn": _decimal_to_float(pmn_data.pmn) if pmn_data else None,
        "pmn_low": _decimal_to_float(pmn_data.pmn_low) if pmn_data else None,
        "pmn_high": _decimal_to_float(pmn_data.pmn_high) if pmn_data else None,
        "sold_history": [
            {
                "date": str(row.date),
                "avg_price": _decimal_to_float(row.avg_price),
                "min_price": _decimal_to_float(row.min_price),
                "max_price": _decimal_to_float(row.max_price),
                "count": row.count,
            }
            for row in sold_history
        ],
        "active_history": [
            {
                "date": str(row.date),
                "avg_price": _decimal_to_float(row.avg_price),
                "min_price": _decimal_to_float(row.min_price),
                "max_price": _decimal_to_float(row.max_price),
                "count": row.count,
            }
            for row in active_history
        ],
    }


@app.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.name).all()
    return {"categories": [_serialize_category(cat) for cat in categories]}


@app.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    if not payload.name.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Category name cannot be empty")

    existing = (
        db.query(Category)
        .filter(func.lower(Category.name) == payload.name.strip().lower())
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Category name already exists")

    category = Category(name=payload.name.strip(), description=payload.description)
    db.add(category)
    db.commit()
    db.refresh(category)
    return _serialize_category(category)


@app.put("/categories/{category_id}")
def update_category(category_id: str, payload: CategoryUpdate, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.category_id == category_id).first()
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")

    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Category name cannot be empty")
        exists = (
            db.query(Category)
            .filter(func.lower(Category.name) == new_name.lower(), Category.category_id != category_id)
            .first()
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Category name already exists")
        category.name = new_name

    if payload.description is not None:
        category.description = payload.description

    db.commit()
    db.refresh(category)
    return _serialize_category(category)


@app.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: str, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.category_id == category_id).first()
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")

    attached_products = (
        db.query(ProductTemplate)
        .filter(ProductTemplate.category_id == category_id)
        .count()
    )
    if attached_products:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Cannot delete category while products are attached",
        )

    db.delete(category)
    db.commit()
    return None


@app.get("/products")
def list_products(
    category_id: str | None = None,
    category_name: str | None = None,
    brand: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(ProductTemplate).join(Category)

    if category_id:
        query = query.filter(ProductTemplate.category_id == category_id)

    if category_name:
        query = query.filter(Category.name.ilike(f"%{category_name}%"))

    if brand:
        query = query.filter(ProductTemplate.brand.ilike(f"%{brand}%"))

    if is_active is not None:
        query = query.filter(ProductTemplate.is_active == is_active)

    products = query.order_by(ProductTemplate.created_at.desc()).all()
    return {"products": [_serialize_product_template(product) for product in products]}


@app.post("/products", status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductTemplateCreate, db: Session = Depends(get_db)):
    category = (
        db.query(Category)
        .filter(Category.category_id == payload.category_id)
        .first()
    )
    if not category:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")

    if payload.price_min is not None and payload.price_max is not None:
        if payload.price_min > payload.price_max:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="price_min cannot be greater than price_max",
            )

    providers = payload.providers or []
    invalid_providers = [p for p in providers if p not in SUPPORTED_PROVIDERS]
    if invalid_providers:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported providers: {', '.join(invalid_providers)}",
        )

    if not payload.name.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Product name cannot be empty")
    if not payload.search_query.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Search query cannot be empty")

    product = ProductTemplate(
        name=payload.name.strip(),
        description=payload.description,
        search_query=payload.search_query.strip(),
        category_id=payload.category_id,
        brand=payload.brand,
        price_min=payload.price_min,
        price_max=payload.price_max,
        providers=providers,
        is_active=payload.is_active,
    )

    db.add(product)
    db.commit()
    db.refresh(product)
    return _serialize_product_template(product)


@app.put("/products/{product_id}")
def update_product(
    product_id: str,
    payload: ProductTemplateUpdate,
    db: Session = Depends(get_db),
):
    product = db.query(ProductTemplate).filter(ProductTemplate.product_id == product_id).first()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    if payload.category_id is not None:
        category = (
            db.query(Category)
            .filter(Category.category_id == payload.category_id)
            .first()
        )
        if not category:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")
        product.category_id = payload.category_id

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Product name cannot be empty")
        product.name = name

    if payload.description is not None:
        product.description = payload.description

    if payload.search_query is not None:
        search_query = payload.search_query.strip()
        if not search_query:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Search query cannot be empty")
        product.search_query = search_query

    if payload.brand is not None:
        product.brand = payload.brand

    if payload.price_min is not None:
        product.price_min = payload.price_min

    if payload.price_max is not None:
        product.price_max = payload.price_max

    if (
        (payload.price_min is not None or payload.price_max is not None)
        and product.price_min is not None
        and product.price_max is not None
        and product.price_min > product.price_max
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="price_min cannot be greater than price_max",
        )

    if payload.providers is not None:
        invalid_providers = [p for p in payload.providers if p not in SUPPORTED_PROVIDERS]
        if invalid_providers:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported providers: {', '.join(invalid_providers)}",
            )
        product.providers = payload.providers

    if payload.is_active is not None:
        product.is_active = payload.is_active

    db.commit()
    db.refresh(product)
    return _serialize_product_template(product)


@app.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: str, db: Session = Depends(get_db)):
    product = db.query(ProductTemplate).filter(ProductTemplate.product_id == product_id).first()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    observations = (
        db.query(ListingObservation)
        .filter(ListingObservation.product_id == product_id)
        .count()
    )
    if observations:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Cannot delete product with existing observations",
        )

    db.delete(product)
    db.commit()
    return None

# Ingestion endpoints
@app.post("/ingestion/trigger")
async def trigger_ingestion(
    product_id: str,
    sold_limit: int = 50,
    listings_limit: int = 50,
    sources: List[str] | None = None,
    db: Session = Depends(get_db),
):
    """Trigger full ingestion pipeline for a product template."""
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and ingestion service are running",
        }

    product = (
        db.query(ProductTemplate)
        .filter(ProductTemplate.product_id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    if sources:
        invalid = [provider for provider in sources if provider not in SUPPORTED_PROVIDERS]
        if invalid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported providers: {', '.join(invalid)}",
            )

    job_sources = sources or product.providers or SUPPORTED_PROVIDERS

    try:
        logger.info(
            "Enqueueing full ingestion for product %s with sources %s",
            product_id,
            job_sources,
        )
        job = await enqueue_arq_job(
            "trigger_full_ingestion",
            product_id,
            sold_limit,
            listings_limit,
            sources=job_sources,
        )
        return {
            "message": f"Ingestion job enqueued for product: {product_id}",
            "status": "enqueued",
            "job_id": job.job_id,
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue ingestion job: {exc}")
        return {"error": "Failed to enqueue job", "message": str(exc)}

@app.post("/ingestion/trigger-sold")
async def trigger_sold_ingestion(
    product_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Trigger eBay sold ingestion for a product template."""
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and ingestion service are running",
        }

    product = (
        db.query(ProductTemplate)
        .filter(ProductTemplate.product_id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    if product.providers and "ebay" not in product.providers:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Product is not configured for the eBay provider",
        )

    try:
        logger.info(f"Enqueueing eBay sold ingestion for product {product_id}")
        job = await enqueue_arq_job("trigger_ebay_sold_ingestion", product_id, limit)
        return {
            "message": f"Sold ingestion job enqueued for product: {product_id}",
            "status": "enqueued",
            "job_id": job.job_id,
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue sold items job: {exc}")
        return {"error": "Failed to enqueue job", "message": str(exc)}

@app.post("/ingestion/trigger-listings")
async def trigger_listings_ingestion(
    product_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Trigger eBay listings ingestion for a product template."""
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and ingestion service are running",
        }

    product = (
        db.query(ProductTemplate)
        .filter(ProductTemplate.product_id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    if product.providers and "ebay" not in product.providers:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Product is not configured for the eBay provider",
        )

    try:
        logger.info(f"Enqueueing eBay listings ingestion for product {product_id}")
        job = await enqueue_arq_job("trigger_ebay_listings_ingestion", product_id, limit)
        return {
            "message": f"Listings ingestion job enqueued for product: {product_id}",
            "status": "enqueued",
            "job_id": job.job_id,
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue listings job: {exc}")
        return {"error": "Failed to enqueue job", "message": str(exc)}

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
    db: Session = Depends(get_db),
):
    """Get filtered listings enriched with product and category context."""

    query = (
        db.query(ListingObservation, ProductTemplate, Category)
        .join(ProductTemplate, ListingObservation.product_id == ProductTemplate.product_id)
        .join(Category, ProductTemplate.category_id == Category.category_id)
    )

    if source:
        query = query.filter(ListingObservation.source == source)

    if category:
        query = query.filter(Category.name.ilike(f"%{category}%"))

    if brand:
        query = query.filter(ProductTemplate.brand.ilike(f"%{brand}%"))

    if price_min is not None:
        query = query.filter(ListingObservation.price >= price_min)

    if price_max is not None:
        query = query.filter(ListingObservation.price <= price_max)

    if condition:
        query = query.filter(ListingObservation.condition.ilike(f"%{condition}%"))

    if not include_sold:
        query = query.filter(ListingObservation.is_sold == False)

    listings = (
        query.order_by(ListingObservation.observed_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for listing, product, category_obj in listings:
        result.append(
            {
                "listing_id": listing.listing_id,
                "title": listing.title,
                "description": listing.description,
                "price": _decimal_to_float(listing.price),
                "currency": listing.currency,
                "condition_raw": listing.condition,
                "condition_norm": normalize_condition(listing.condition),
                "location": listing.location,
                "seller_rating": _decimal_to_float(listing.seller_rating),
                "shipping_cost": _decimal_to_float(listing.shipping_cost),
                "observed_at": listing.observed_at.isoformat() if listing.observed_at else None,
                "is_sold": listing.is_sold,
                "source": listing.source,
                "product_id": str(product.product_id),
                "product_name": product.name,
                "category": category_obj.name if category_obj else None,
                "category_id": str(category_obj.category_id) if category_obj else None,
                "url": None,
            }
        )

    return {"listings": result}

@app.get("/ingestion/status")
async def ingestion_status(db: Session = Depends(get_db)):
    """Get current ingestion status and statistics"""

    total_products = db.query(ProductTemplate).count()
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
    product_id: str,
    listings_limit: int = 50,
    db: Session = Depends(get_db),
):
    """Trigger LeBonCoin ingestion pipeline for a product."""
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and ingestion service are running",
        }

    product = _get_product_or_404(db, product_id)
    _ensure_provider_allowed(product, "leboncoin")

    try:
        logger.info(f"Enqueueing LeBonCoin ingestion for product {product_id}")
        job = await enqueue_arq_job(
            "trigger_full_ingestion",
            product_id,
            listings_limit,
            listings_limit,
            sources=["leboncoin"],
        )
        logger.info(f"Successfully enqueued job with ID: {job.job_id}")
        return {
            "message": f"LeBonCoin ingestion job enqueued for product: {product_id}",
            "status": "enqueued",
            "job_id": job.job_id,
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue LeBonCoin job: {exc}", exc_info=True)
        return {"error": "Failed to enqueue job", "message": str(exc)}

@app.post("/ingestion/leboncoin/trigger-listings")
async def trigger_leboncoin_listings_ingestion_endpoint(
    product_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Trigger LeBonCoin listings ingestion for a product."""
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and ingestion service are running",
        }

    product = _get_product_or_404(db, product_id)
    _ensure_provider_allowed(product, "leboncoin")

    try:
        logger.info(f"Enqueueing LeBonCoin listings ingestion for product {product_id}")
        job = await enqueue_arq_job(
            "trigger_leboncoin_listings_ingestion", product_id, limit
        )
        return {
            "message": f"LeBonCoin listings ingestion job enqueued for product: {product_id}",
            "status": "enqueued",
            "job_id": job.job_id,
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue LeBonCoin listings job: {exc}")
        return {"error": "Failed to enqueue job", "message": str(exc)}

@app.post("/ingestion/leboncoin/trigger-sold")
async def trigger_leboncoin_sold_ingestion_endpoint(
    product_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Trigger LeBonCoin 'sold' ingestion for a product."""
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and ingestion service are running",
        }

    product = _get_product_or_404(db, product_id)
    _ensure_provider_allowed(product, "leboncoin")

    try:
        logger.info(f"Enqueueing LeBonCoin 'sold' ingestion for product {product_id}")
        job = await enqueue_arq_job(
            "trigger_leboncoin_sold_ingestion", product_id, limit
        )
        return {
            "message": f"LeBonCoin 'sold' ingestion job enqueued for product: {product_id}",
            "status": "enqueued",
            "job_id": job.job_id,
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue LeBonCoin sold job: {exc}")
        return {"error": "Failed to enqueue job", "message": str(exc)}

# Vinted-specific endpoints
@app.post("/ingestion/vinted/trigger")
async def trigger_vinted_ingestion(
    product_id: str,
    listings_limit: int = 50,
    db: Session = Depends(get_db),
):
    """Trigger Vinted ingestion pipeline for a product."""
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and ingestion service are running",
        }

    product = _get_product_or_404(db, product_id)
    _ensure_provider_allowed(product, "vinted")

    try:
        logger.info(f"Enqueueing Vinted ingestion for product {product_id}")
        job = await enqueue_arq_job(
            "trigger_full_ingestion",
            product_id,
            listings_limit,
            listings_limit,
            sources=["vinted"],
        )
        return {
            "message": f"Vinted ingestion job enqueued for product: {product_id}",
            "status": "enqueued",
            "job_id": job.job_id,
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue Vinted job: {exc}")
        return {"error": "Failed to enqueue job", "message": str(exc)}

@app.post("/ingestion/vinted/trigger-listings")
async def trigger_vinted_listings_ingestion_endpoint(
    product_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Trigger Vinted listings ingestion for a product."""
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and ingestion service are running",
        }

    product = _get_product_or_404(db, product_id)
    _ensure_provider_allowed(product, "vinted")

    try:
        logger.info(f"Enqueueing Vinted listings ingestion for product {product_id}")
        job = await enqueue_arq_job("trigger_vinted_listings_ingestion", product_id, limit)
        return {
            "message": f"Vinted listings ingestion job enqueued for product: {product_id}",
            "status": "enqueued",
            "job_id": job.job_id,
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue Vinted listings job: {exc}")
        return {"error": "Failed to enqueue job", "message": str(exc)}

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


# ============================================================================
# ANALYTICS & DISCOVERY ENHANCEMENTS
# ============================================================================

@app.get("/analytics/overview")
def analytics_overview(db: Session = Depends(get_db)):
    """
    Get high-level analytics for the dashboard.
    Returns key metrics across all products and opportunities.
    """
    # Total active products
    total_products = db.query(ProductTemplate).filter(ProductTemplate.is_active == True).count()
    
    # Total observations
    total_observations = db.query(ListingObservation).count()
    
    # Active listings
    active_listings = db.query(ListingObservation).filter(ListingObservation.is_sold == False).count()
    
    # Sold items
    sold_items = db.query(ListingObservation).filter(ListingObservation.is_sold == True).count()
    
    # Products with PMN calculated
    products_with_pmn = db.query(MarketPriceNormal).count()
    
    # Calculate opportunity count (products with active listings below PMN)
    opportunities_query = (
        db.query(ProductTemplate.product_id)
        .join(MarketPriceNormal, ProductTemplate.product_id == MarketPriceNormal.product_id)
        .join(
            ListingObservation,
            and_(
                ProductTemplate.product_id == ListingObservation.product_id,
                ListingObservation.is_sold == False,
                ListingObservation.price.isnot(None),
            ),
        )
        .filter(
            ProductTemplate.is_active == True,
            MarketPriceNormal.pmn.isnot(None),
        )
        .group_by(ProductTemplate.product_id, MarketPriceNormal.pmn)
        .having(func.min(ListingObservation.price) < MarketPriceNormal.pmn)
    )
    
    opportunities_count = opportunities_query.count()
    
    # Provider breakdown
    ebay_count = db.query(ListingObservation).filter(ListingObservation.source == "ebay").count()
    leboncoin_count = db.query(ListingObservation).filter(ListingObservation.source == "leboncoin").count()
    vinted_count = db.query(ListingObservation).filter(ListingObservation.source == "vinted").count()
    
    # Recent activity (last 24 hours)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    recent_observations = (
        db.query(ListingObservation)
        .filter(ListingObservation.observed_at >= yesterday)
        .count()
    )
    
    return {
        "total_products": total_products,
        "products_with_pmn": products_with_pmn,
        "total_observations": total_observations,
        "active_listings": active_listings,
        "sold_items": sold_items,
        "opportunities_count": opportunities_count,
        "recent_observations_24h": recent_observations,
        "providers": {
            "ebay": ebay_count,
            "leboncoin": leboncoin_count,
            "vinted": vinted_count,
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/analytics/top-opportunities")
def top_opportunities(
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """
    Get top arbitrage opportunities by margin %.
    Quick access to best deals.
    """
    # Reuse discovery logic with strict filters
    query = (
        db.query(
            ProductTemplate,
            MarketPriceNormal,
            ProductDailyMetrics,
            func.min(ListingObservation.price).label("best_price"),
        )
        .join(MarketPriceNormal, ProductTemplate.product_id == MarketPriceNormal.product_id)
        .outerjoin(
            ProductDailyMetrics,
            and_(
                ProductTemplate.product_id == ProductDailyMetrics.product_id,
                ProductDailyMetrics.date == func.current_date(),
            ),
        )
        .join(
            ListingObservation,
            and_(
                ProductTemplate.product_id == ListingObservation.product_id,
                ListingObservation.is_sold == False,
                ListingObservation.price.isnot(None),
            ),
        )
        .filter(
            ProductTemplate.is_active == True,
            MarketPriceNormal.pmn.isnot(None),
        )
        .group_by(
            ProductTemplate.product_id,
            MarketPriceNormal.product_id,
            ProductDailyMetrics.product_id,
            ProductDailyMetrics.date,
        )
        .having(func.min(ListingObservation.price) < MarketPriceNormal.pmn)
    )
    
    results = query.all()
    
    # Calculate margins and sort
    opportunities = []
    for product, pmn, metrics, best_price in results:
        pmn_value = _decimal_to_float(pmn.pmn)
        best_price_value = _decimal_to_float(best_price)
        
        if pmn_value and best_price_value:
            delta_pct = ((best_price_value - pmn_value) / pmn_value * 100)
            margin_abs = pmn_value - best_price_value
            
            opportunities.append({
                "product_id": str(product.product_id),
                "title": product.name,
                "brand": product.brand,
                "pmn": pmn_value,
                "best_price": best_price_value,
                "delta_pct": round(delta_pct, 2),
                "margin_abs": round(margin_abs, 2),
                "liquidity_score": _decimal_to_float(metrics.liquidity_score) if metrics else None,
            })
    
    # Sort by margin %
    opportunities.sort(key=lambda x: x["delta_pct"])
    
    return {
        "opportunities": opportunities[:limit],
        "total_found": len(opportunities),
    }


# ============================================================================
# LISTING EXPLORATION
# ============================================================================

@app.get("/listings/explore")
def explore_listings(
    source: str | None = Query(None, description="Filter by source: ebay, leboncoin, vinted"),
    is_sold: bool | None = Query(None, description="Filter by sold status"),
    product_id: str | None = Query(None, description="Filter by product ID"),
    min_price: float | None = Query(None, description="Minimum price"),
    max_price: float | None = Query(None, description="Maximum price"),
    search: str | None = Query(None, description="Search in title"),
    sort_by: str = Query("observed_at", description="Sort by: price, observed_at"),
    sort_order: str = Query("desc", description="Sort order: asc, desc"),
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    Explore all ingested listings with flexible filtering.
    Great for debugging ingestion and understanding market data.
    """
    # Base query with product join for context
    query = db.query(
        ListingObservation,
        ProductTemplate.name.label("product_name"),
        ProductTemplate.brand.label("product_brand"),
    ).join(
        ProductTemplate,
        ListingObservation.product_id == ProductTemplate.product_id
    )
    
    # Apply filters
    if source:
        query = query.filter(ListingObservation.source == source)
    
    if is_sold is not None:
        query = query.filter(ListingObservation.is_sold == is_sold)
    
    if product_id:
        query = query.filter(ListingObservation.product_id == product_id)
    
    if min_price is not None:
        query = query.filter(ListingObservation.price >= min_price)
    
    if max_price is not None:
        query = query.filter(ListingObservation.price <= max_price)
    
    if search:
        query = query.filter(ListingObservation.title.ilike(f"%{search}%"))
    
    # Get total count before pagination
    total = query.count()
    
    # Apply sorting
    if sort_by == "price":
        sort_col = ListingObservation.price
    elif sort_by == "observed_at":
        sort_col = ListingObservation.observed_at
    else:
        sort_col = ListingObservation.observed_at
    
    if sort_order == "desc":
        query = query.order_by(desc(sort_col))
    else:
        query = query.order_by(sort_col)
    
    # Apply pagination
    query = query.offset(offset).limit(limit)
    
    # Execute query
    results = query.all()
    
    # Format response
    listings = []
    for observation, product_name, product_brand in results:
        listings.append({
            "obs_id": observation.obs_id,
            "product_id": str(observation.product_id),
            "product_name": product_name,
            "product_brand": product_brand,
            "source": observation.source,
            "listing_id": observation.listing_id,
            "title": observation.title,
            "description": observation.description,
            "price": _decimal_to_float(observation.price),
            "currency": observation.currency,
            "condition": observation.condition,
            "is_sold": observation.is_sold,
            "seller_rating": _decimal_to_float(observation.seller_rating),
            "shipping_cost": _decimal_to_float(observation.shipping_cost),
            "location": observation.location,
            "observed_at": observation.observed_at.isoformat() if observation.observed_at else None,
            "url": observation.url,
        })
    
    return {
        "listings": listings,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ============================================================================
# COMPUTATION ENGINE ENDPOINTS
# ============================================================================

@app.post("/computation/trigger-all")
async def trigger_all_computation(db: Session = Depends(get_db)):
    """
    Trigger PMN and metrics computation for all active products.
    This is useful for manual recalculation or initial setup.
    """
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and worker service are running"
        }
    
    try:
        logger.info("Triggering batch computation for all active products")
        job = await enqueue_arq_job("trigger_batch_computation")
        
        return {
            "message": "Batch computation job enqueued for all active products",
            "status": "enqueued",
            "job_id": job.job_id
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue batch computation: {exc}", exc_info=True)
        return {"error": "Failed to enqueue job", "message": str(exc)}


@app.post("/computation/trigger/{product_id}")
async def trigger_product_computation_endpoint(
    product_id: str,
    db: Session = Depends(get_db)
):
    """
    Trigger PMN and metrics computation for a specific product.
    Computes:
    - PMN (Price of Market Normal)
    - Liquidity score
    - Daily metrics
    """
    if not arq_pool:
        return {
            "error": "ARQ pool not available",
            "message": "Please ensure Redis and worker service are running"
        }
    
    # Verify product exists
    product = _get_product_or_404(db, product_id)
    
    try:
        logger.info(f"Triggering computation for product {product_id}")
        job = await enqueue_arq_job("trigger_product_computation", product_id)
        
        return {
            "message": f"Computation job enqueued for product: {product.name}",
            "status": "enqueued",
            "job_id": job.job_id,
            "product_id": product_id
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue computation job: {exc}", exc_info=True)
        return {"error": "Failed to enqueue job", "message": str(exc)}


@app.get("/listings/{obs_id}/opportunity")
def get_listing_opportunity_score(obs_id: int, db: Session = Depends(get_db)):
    """
    Calculate and return the opportunity score for a specific listing.
    
    Returns detailed breakdown including:
    - Opportunity score (0-100)
    - Margin analysis (gross/net margins, fees)
    - Risk assessment
    - Recommendation (strong_buy, good_buy, fair, pass)
    """
    # Import here to avoid circular dependency
    from ingestion.computation import compute_opportunity_score
    
    # Get listing
    listing = db.query(ListingObservation).filter(
        ListingObservation.obs_id == obs_id
    ).first()
    
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Listing with ID {obs_id} not found"
        )
    
    # Get product metrics and PMN
    product_metrics = db.query(ProductDailyMetrics).filter(
        ProductDailyMetrics.product_id == listing.product_id
    ).order_by(desc(ProductDailyMetrics.date)).first()
    
    pmn_data = db.query(MarketPriceNormal).filter(
        MarketPriceNormal.product_id == listing.product_id
    ).first()
    
    # Compute opportunity score
    opportunity = compute_opportunity_score(listing, product_metrics, pmn_data)
    
    # Add listing context
    opportunity["listing"] = {
        "obs_id": listing.obs_id,
        "title": listing.title,
        "description": listing.description,
        "price": _decimal_to_float(listing.price),
        "source": listing.source,
        "url": listing.url,
        "condition": listing.condition,
        "seller_rating": _decimal_to_float(listing.seller_rating),
        "shipping_cost": _decimal_to_float(listing.shipping_cost)
    }
    
    opportunity["pmn"] = {
        "value": _decimal_to_float(pmn_data.pmn) if pmn_data else None,
        "pmn_low": _decimal_to_float(pmn_data.pmn_low) if pmn_data else None,
        "pmn_high": _decimal_to_float(pmn_data.pmn_high) if pmn_data else None,
        "last_computed": pmn_data.last_computed_at.isoformat() if pmn_data and pmn_data.last_computed_at else None
    }
    
    return opportunity


@app.get("/computation/status")
def get_computation_status(db: Session = Depends(get_db)):
    """
    Get computation engine status and statistics.
    
    Returns:
    - Number of products with PMN computed
    - Number of products with recent metrics
    - Last computation timestamps
    - Data quality indicators
    """
    from datetime import date
    
    # Total active products
    total_products = db.query(ProductTemplate).filter(
        ProductTemplate.is_active == True
    ).count()
    
    # Products with PMN
    products_with_pmn = db.query(MarketPriceNormal).count()
    
    # Products with today's metrics
    products_with_today_metrics = db.query(ProductDailyMetrics).filter(
        ProductDailyMetrics.date == date.today()
    ).count()
    
    # Recent PMN computations (last 24 hours)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    recent_pmn_updates = db.query(MarketPriceNormal).filter(
        MarketPriceNormal.last_computed_at >= yesterday
    ).count()
    
    # Get latest PMN computation
    latest_pmn = db.query(MarketPriceNormal).order_by(
        desc(MarketPriceNormal.last_computed_at)
    ).first()
    
    # Average liquidity score
    avg_liquidity = db.query(func.avg(ProductDailyMetrics.liquidity_score)).filter(
        ProductDailyMetrics.date == date.today()
    ).scalar()
    
    return {
        "total_active_products": total_products,
        "products_with_pmn": products_with_pmn,
        "products_with_today_metrics": products_with_today_metrics,
        "pmn_coverage_pct": round((products_with_pmn / total_products * 100), 2) if total_products > 0 else 0,
        "recent_pmn_updates_24h": recent_pmn_updates,
        "latest_pmn_computation": latest_pmn.last_computed_at.isoformat() if latest_pmn and latest_pmn.last_computed_at else None,
        "average_liquidity_score": round(float(avg_liquidity), 2) if avg_liquidity else None,
        "last_updated": datetime.now(timezone.utc).isoformat()
    }

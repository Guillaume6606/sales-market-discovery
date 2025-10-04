from typing import List, Dict, Any
import asyncio
from datetime import datetime, timezone, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, extract
from loguru import logger

from libs.common.models import ProductRef, ListingObservation, ProductDailyMetrics, MarketPriceNormal, Listing
from libs.common.db import SessionLocal
from ingestion.connectors.ebay import fetch_ebay_sold, fetch_ebay_listings, parse_ebay_response
from ingestion.connectors.leboncoin_api import (
    fetch_leboncoin_api_listings,
    fetch_leboncoin_api_sold,
)
from ingestion.connectors.vinted import fetch_vinted_listings, fetch_vinted_sold
from ingestion.pricing import pmn_from_prices

async def ingest_ebay_sold(keyword: str, limit: int = 50) -> Dict[str, Any]:
    """Ingest sold items from eBay for a given keyword"""
    logger.info(f"Starting eBay sold items ingestion for keyword: {keyword}")

    try:
        # Fetch data from eBay
        items = await fetch_ebay_sold(keyword, limit)

        if not items:
            logger.warning(f"No sold items found for keyword: {keyword}")
            return {"status": "no_data", "count": 0}

        # Process and store items
        processed_count = 0
        with SessionLocal() as db:
            for item in items:
                try:
                    # Create or get product reference
                    product = get_or_create_product(db, item, keyword)

                    # Create listing observation
                    observation = ListingObservation(
                        product_id=product.product_id,
                        source=item.source.value,
                        listing_id=item.listing_id,
                        title=item.title,
                        price=item.price,
                        currency=item.currency,
                        condition=item.condition_raw,
                        is_sold=item.is_sold,
                        seller_rating=item.seller_rating,
                        shipping_cost=item.shipping_cost,
                        location=item.location,
                        observed_at=item.observed_at
                    )
                    db.add(observation)
                    processed_count += 1

                except Exception as e:
                    logger.error(f"Error processing sold item {item.listing_id}: {e}")
                    continue

            db.commit()

        logger.info(f"Successfully ingested {processed_count} sold items for keyword: {keyword}")
        return {"status": "success", "count": processed_count}

    except Exception as e:
        logger.error(f"Error in eBay sold items ingestion: {e}")
        return {"status": "error", "error": str(e)}

async def ingest_ebay_listings(keyword: str, limit: int = 50) -> Dict[str, Any]:
    """Ingest current listings from eBay for a given keyword"""
    logger.info(f"Starting eBay listings ingestion for keyword: {keyword}")

    try:
        # Fetch data from eBay
        items = await fetch_ebay_listings(keyword, limit)

        if not items:
            logger.warning(f"No listings found for keyword: {keyword}")
            return {"status": "no_data", "count": 0}

        # Process and store items
        processed_count = 0
        with SessionLocal() as db:
            for item in items:
                try:
                    # Create or get product reference
                    product = get_or_create_product(db, item, keyword)

                    # Check if we already have this listing to avoid duplicates
                    existing = db.query(ListingObservation).filter(
                        and_(
                            ListingObservation.listing_id == item.listing_id,
                            ListingObservation.source == item.source.value,
                            ListingObservation.is_sold == False
                        )
                    ).first()

                    if existing:
                        # Update existing listing
                        existing.price = item.price
                        existing.title = item.title
                        existing.condition = item.condition_raw
                        existing.seller_rating = item.seller_rating
                        existing.shipping_cost = item.shipping_cost
                        existing.observed_at = item.observed_at
                    else:
                        # Create new listing observation
                        observation = ListingObservation(
                            product_id=product.product_id,
                            source=item.source.value,
                            listing_id=item.listing_id,
                            title=item.title,
                            price=item.price,
                            currency=item.currency,
                            condition=item.condition_raw,
                            is_sold=item.is_sold,
                            seller_rating=item.seller_rating,
                            shipping_cost=item.shipping_cost,
                            location=item.location,
                            observed_at=item.observed_at
                        )
                        db.add(observation)

                    processed_count += 1

                except Exception as e:
                    logger.error(f"Error processing listing {item.listing_id}: {e}")
                    continue

            db.commit()

        logger.info(f"Successfully ingested {processed_count} listings for keyword: {keyword}")
        return {"status": "success", "count": processed_count}

    except Exception as e:
        logger.error(f"Error in eBay listings ingestion: {e}")
        return {"status": "error", "error": str(e)}

async def ingest_leboncoin_listings(keyword: str, limit: int = 50) -> Dict[str, Any]:
    """Ingest current listings from LeBonCoin for a given keyword"""
    logger.info(f"Starting LeBonCoin listings ingestion for keyword: {keyword}")

    try:
        # Fetch data from LeBonCoin
        items = await fetch_leboncoin_api_listings(keyword, limit)

        if not items:
            logger.warning(f"No listings found for keyword: {keyword}")
            return {"status": "no_data", "count": 0}

        # Process and store items
        processed_count = 0
        with SessionLocal() as db:
            for item in items:
                try:
                    source = item.source if isinstance(item, Listing) else item.get("source")
                    listing_id = item.listing_id if isinstance(item, Listing) else item.get("listing_id")
                    if not source:
                        source = "leboncoin"

                    if not listing_id:
                        logger.warning("Skipping LeBonCoin listing without listing_id")
                        continue

                    if isinstance(item, Listing):
                        observed_at = item.observed_at
                        price = item.price
                        title = item.title
                        currency = item.currency
                        condition = item.condition_raw
                        seller_rating = item.seller_rating
                        location = item.location
                    else:
                        title = item.get("title", "")
                        price = item.get("price")
                        currency = item.get("currency", "EUR")
                        condition = item.get("condition", "Unknown")
                        seller_rating = 1.0 if item.get("is_pro") else 0.0
                        location = item.get("location")
                        observed_raw = item.get("observed_at")
                        if observed_raw:
                            observed_at = datetime.fromisoformat(observed_raw.replace('Z', '+00:00'))
                        else:
                            observed_at = datetime.now(timezone.utc)

                    # Create or get product reference
                    product = get_or_create_product_leboncoin(db, item, keyword)

                    # Check if we already have this listing to avoid duplicates
                    existing = db.query(ListingObservation).filter(
                        and_(
                            ListingObservation.listing_id == listing_id,
                            ListingObservation.source == source,
                            ListingObservation.is_sold == False
                        )
                    ).first()

                    if existing:
                        # Update existing listing
                        existing.price = price
                        existing.title = title
                        existing.location = location
                        existing.observed_at = observed_at
                    else:
                        # Create new listing observation
                        observation = ListingObservation(
                            product_id=product.product_id,
                            source=source,
                            listing_id=listing_id,
                            title=title,
                            price=price,
                            currency=currency,
                            condition=condition,
                            is_sold=False,
                            seller_rating=seller_rating,
                            location=location,
                            observed_at=observed_at
                        )
                        db.add(observation)

                    processed_count += 1

                except Exception as e:
                    logger.error(f"Error processing LeBonCoin listing {listing_id}: {e}")
                    continue

            db.commit()

        logger.info(f"Successfully ingested {processed_count} LeBonCoin listings for keyword: {keyword}")
        return {"status": "success", "count": processed_count}

    except Exception as e:
        logger.error(f"Error in LeBonCoin listings ingestion: {e}")
        return {"status": "error", "error": str(e)}

async def ingest_leboncoin_sold(keyword: str, limit: int = 50) -> Dict[str, Any]:
    """Ingest 'sold' items from LeBonCoin for a given keyword"""
    logger.info(f"Starting LeBonCoin 'sold' items ingestion for keyword: {keyword}")

    try:
        # Fetch data from LeBonCoin (note: LeBonCoin doesn't have direct sold data)
        items = await fetch_leboncoin_api_sold(keyword, limit)

        if not items:
            logger.warning(f"No items found for keyword: {keyword}")
            return {"status": "no_data", "count": 0}

        # Process and store items as sold items
        processed_count = 0
        with SessionLocal() as db:
            for item in items:
                try:
                    source = item.source if isinstance(item, Listing) else item.get("source")
                    listing_id = item.listing_id if isinstance(item, Listing) else item.get("listing_id")
                    if not source:
                        source = "leboncoin"
                    if not listing_id:
                        logger.warning("Skipping LeBonCoin sold item without listing_id")
                        continue

                    if isinstance(item, Listing):
                        title = item.title
                        price = item.price
                        currency = item.currency
                        condition = item.condition_raw
                        seller_rating = item.seller_rating
                        location = item.location
                        observed_at = item.observed_at
                    else:
                        title = item.get("title", "")
                        price = item.get("price")
                        currency = item.get("currency", "EUR")
                        condition = item.get("condition", "Unknown")
                        seller_rating = 1.0 if item.get("is_pro") else 0.0
                        location = item.get("location")
                        observed_raw = item.get("observed_at")
                        if observed_raw:
                            observed_at = datetime.fromisoformat(observed_raw.replace('Z', '+00:00'))
                        else:
                            observed_at = datetime.now(timezone.utc)

                    # Create or get product reference
                    product = get_or_create_product_leboncoin(db, item, keyword)

                    # Create listing observation as sold
                    observation = ListingObservation(
                        product_id=product.product_id,
                        source=source,
                        listing_id=listing_id,
                        title=title,
                        price=price,
                        currency=currency,
                        condition=condition,
                        is_sold=True,  # Mark as sold
                        seller_rating=seller_rating,
                        location=location,
                        observed_at=observed_at
                    )
                    db.add(observation)
                    processed_count += 1

                except Exception as e:
                    listing_ref = str(listing_id) if listing_id else "unknown"
                    logger.error(f"Error processing LeBonCoin sold item {listing_ref}: {e}")
                    continue

            db.commit()

        logger.info(f"Successfully ingested {processed_count} LeBonCoin 'sold' items for keyword: {keyword}")
        return {"status": "success", "count": processed_count}

    except Exception as e:
        logger.error(f"Error in LeBonCoin 'sold' items ingestion: {e}")
        return {"status": "error", "error": str(e)}

async def ingest_vinted_listings(keyword: str, limit: int = 50) -> Dict[str, Any]:
    """Ingest current listings from Vinted for a given keyword"""
    logger.info(f"Starting Vinted listings ingestion for keyword: {keyword}")

    try:
        # Fetch data from Vinted
        items = await fetch_vinted_listings(keyword, limit)

        if not items:
            logger.warning(f"No listings found for keyword: {keyword}")
            return {"status": "no_data", "count": 0}

        # Process and store items
        processed_count = 0
        with SessionLocal() as db:
            for item in items:
                try:
                    # Create or get product reference
                    product = get_or_create_product_vinted(db, item, keyword)

                    # Check if we already have this listing to avoid duplicates
                    existing = db.query(ListingObservation).filter(
                        and_(
                            ListingObservation.listing_id == item.listing_id,
                            ListingObservation.source == item.source.value,
                            ListingObservation.is_sold == False
                        )
                    ).first()

                    if existing:
                        # Update existing listing
                        existing.price = item.price
                        existing.title = item.title
                        existing.location = item.location
                        existing.observed_at = item.observed_at
                    else:
                        # Create new listing observation
                        observation = ListingObservation(
                            product_id=product.product_id,
                            source=item.source.value,
                            listing_id=item.listing_id,
                            title=item.title,
                            price=item.price,
                            currency=item.currency,
                            condition=item.condition_raw,
                            is_sold=False,
                            seller_rating=item.seller_rating,
                            location=item.location,
                            observed_at=item.observed_at
                        )
                        db.add(observation)

                    processed_count += 1

                except Exception as e:
                    logger.error(f"Error processing Vinted listing {item.listing_id}: {e}")
                    continue

            db.commit()

        logger.info(f"Successfully ingested {processed_count} Vinted listings for keyword: {keyword}")
        return {"status": "success", "count": processed_count}

    except Exception as e:
        logger.error(f"Error in Vinted listings ingestion: {e}")
        return {"status": "error", "error": str(e)}

async def ingest_vinted_sold(keyword: str, limit: int = 50) -> Dict[str, Any]:
    """Ingest 'sold' items from Vinted for a given keyword"""
    logger.info(f"Starting Vinted 'sold' items ingestion for keyword: {keyword}")

    try:
        # Fetch data from Vinted (note: Vinted doesn't have direct sold data)
        items = await fetch_vinted_sold(keyword, limit)

        if not items:
            logger.warning(f"No items found for keyword: {keyword}")
            return {"status": "no_data", "count": 0}

        # Process and store items as sold items
        processed_count = 0
        with SessionLocal() as db:
            for item in items:
                try:
                    # Create or get product reference
                    product = get_or_create_product_vinted(db, item, keyword)

                    # Create listing observation as sold
                    observation = ListingObservation(
                        product_id=product.product_id,
                        source=item.source.value,
                        listing_id=item.listing_id,
                        title=item.title,
                        price=item.price,
                        currency=item.currency,
                        condition=item.condition_raw,
                        is_sold=True,  # Mark as sold
                        seller_rating=item.seller_rating,
                        location=item.location,
                        observed_at=item.observed_at
                    )
                    db.add(observation)
                    processed_count += 1

                except Exception as e:
                    logger.error(f"Error processing Vinted sold item {item.listing_id}: {e}")
                    continue

            db.commit()

        logger.info(f"Successfully ingested {processed_count} Vinted 'sold' items for keyword: {keyword}")
        return {"status": "success", "count": processed_count}

    except Exception as e:
        logger.error(f"Error in Vinted 'sold' items ingestion: {e}")
        return {"status": "error", "error": str(e)}

def get_or_create_product(db: Session, item: Dict[str, Any], keyword: str) -> ProductRef:
    """Get existing product or create new one based on item data"""
    # Try to find existing product by listing_id
    existing_obs = db.query(ListingObservation).filter(
        ListingObservation.listing_id == item["listing_id"]
    ).first()

    if existing_obs:
        return existing_obs.product

    # Try to find product by title similarity or create new
    product = ProductRef(
        canonical_title=item["title"],
        brand=extract_brand_from_title(item["title"]),
        category=keyword,  # Use keyword as category for now
        created_at=datetime.now(timezone.utc)
    )
    db.add(product)
    db.flush()  # Get the product_id

    return product

def get_or_create_product_leboncoin(db: Session, item: Listing | Dict[str, Any], keyword: str) -> ProductRef:
    """Get existing product or create new one based on LeBonCoin item data"""
    if isinstance(item, Listing):
        listing_id = item.listing_id
        title = item.title
    else:
        listing_id = item.get("listing_id")
        title = item.get("title", "")

    if not listing_id:
        raise ValueError("Listing id is required to create or lookup product")

    # Try to find existing product by listing_id
    existing_obs = db.query(ListingObservation).filter(
        ListingObservation.listing_id == listing_id
    ).first()

    if existing_obs:
        return existing_obs.product

    # Extract brand from LeBonCoin specific data
    brand = "Unknown"
    if title:
        brand = extract_brand_from_title_leboncoin(title)

    # Try to find product by title similarity or create new
    product = ProductRef(
        canonical_title=title,
        brand=brand,
        category=keyword,  # Use keyword as category for now
        created_at=datetime.now(timezone.utc)
    )
    db.add(product)
    db.flush()  # Get the product_id

    return product

def get_or_create_product_vinted(db: Session, item: Listing, keyword: str) -> ProductRef:
    """Get existing product or create new one based on Vinted item data"""
    # Try to find existing product by listing_id
    existing_obs = db.query(ListingObservation).filter(
        ListingObservation.listing_id == item.listing_id
    ).first()

    if existing_obs:
        return existing_obs.product

    # Extract brand from Vinted specific data
    brand = item.brand or "Unknown"
    if not brand and item.title:
        brand = extract_brand_from_title_vinted(item.title)

    # Try to find product by title similarity or create new
    product = ProductRef(
        canonical_title=item.title,
        brand=brand,
        category=keyword,  # Use keyword as category for now
        created_at=datetime.now(timezone.utc)
    )
    db.add(product)
    db.flush()  # Get the product_id

    return product

def extract_brand_from_title(title: str) -> str:
    """Extract brand from product title (simple implementation)"""
    # This is a very basic implementation - could be improved with NLP
    title_lower = title.lower()

    # Common brand indicators
    brand_indicators = ["sony", "apple", "samsung", "lg", "panasonic", "canon", "nikon", "bose"]

    for brand in brand_indicators:
        if brand in title_lower:
            return brand.title()

    return "Unknown"

def extract_brand_from_title_leboncoin(title: str) -> str:
    """Extract brand from LeBonCoin product title (French market focus)"""
    # This is a very basic implementation - could be improved with NLP
    title_lower = title.lower()

    # French/International brand indicators
    french_brands = [
        "sony", "apple", "samsung", "lg", "panasonic", "canon", "nikon", "bose",
        "nintendo", "playstation", "xbox", "microsoft", "dell", "hp", "lenovo",
        "asus", "acer", "toshiba", "sony", "jvc", "pioneer", "yamaha"
    ]

    for brand in french_brands:
        if brand in title_lower:
            return brand.title()

    return "Unknown"

def extract_brand_from_title_vinted(title: str) -> str:
    """Extract brand from Vinted product title (fashion focus)"""
    # This is a very basic implementation - could be improved with NLP
    title_lower = title.lower()

    # Fashion brand indicators
    fashion_brands = [
        "nike", "adidas", "puma", "reebok", "converse", "vans", "new balance",
        "h&m", "zara", "uniqlo", "gap", "levi's", "dickies", "carhartt",
        "supreme", "stone island", "moncler", "canada goose", "patagonia",
        "the north face", "columbia", "arcteryx", "marmot", "salomon",
        "asics", "brooks", "saucony", "mizuno", "hoka", "on running"
    ]

    for brand in fashion_brands:
        if brand in title_lower:
            return brand.title()

    return "Unknown"

def calculate_daily_metrics(product_id: str) -> Dict[str, Any]:
    """Calculate daily metrics for a product"""
    with SessionLocal() as db:
        today = date.today()
        now_utc = datetime.now(timezone.utc)

        # Get sold items from last 30 days
        thirty_days_ago = now_utc - timedelta(days=30)

        sold_items = db.query(ListingObservation).filter(
            and_(
                ListingObservation.product_id == product_id,
                ListingObservation.is_sold == True,
                ListingObservation.observed_at >= thirty_days_ago
            )
        ).all()

        if not sold_items:
            return {
                "sold_count_7d": 0,
                "sold_count_30d": 0,
                "price_median": None,
                "price_std": None,
                "price_p25": None,
                "price_p75": None,
                "liquidity_score": 0.0,
                "trend_score": 0.0
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
            "price_std": pmn_data.get("pmn_high", 0) - pmn_data.get("pmn_low", 0) if pmn_data["pmn"] else 0,
            "price_p25": min(prices) if prices else None,
            "price_p75": max(prices) if prices else None,
            "liquidity_score": liquidity_score,
            "trend_score": trend_score
        }

def update_product_metrics(product_id: str) -> None:
    """Update or create daily metrics for a product"""
    metrics_data = calculate_daily_metrics(product_id)

    with SessionLocal() as db:
        # Check if metrics already exist for today
        existing = db.query(ProductDailyMetrics).filter(
            and_(
                ProductDailyMetrics.product_id == product_id,
                ProductDailyMetrics.date == date.today()
            )
        ).first()

        if existing:
            # Update existing metrics
            for key, value in metrics_data.items():
                setattr(existing, key, value)
        else:
            # Create new metrics
            new_metrics = ProductDailyMetrics(
                product_id=product_id,
                date=date.today(),
                **metrics_data
            )
            db.add(new_metrics)

        db.commit()

async def run_full_ingestion(keyword: str, limits: Dict[str, int] = None, sources: List[str] = None) -> Dict[str, Any]:
    """Run full ingestion pipeline for a keyword"""
    if limits is None:
        limits = {"ebay_sold": 50, "ebay_listings": 50, "leboncoin_listings": 50, "vinted_listings": 50}
    if sources is None:
        sources = ["ebay", "leboncoin", "vinted"]

    logger.info(f"Starting full ingestion for keyword: {keyword} from sources: {sources}")

    results = {"keyword": keyword}

    # eBay ingestion
    if "ebay" in sources:
        results["ebay_sold"] = await ingest_ebay_sold(keyword, limits.get("ebay_sold", 50))
        results["ebay_listings"] = await ingest_ebay_listings(keyword, limits.get("ebay_listings", 50))

    # LeBonCoin ingestion
    if "leboncoin" in sources:
        results["leboncoin_listings"] = await ingest_leboncoin_listings(keyword, limits.get("leboncoin_listings", 50))
        results["leboncoin_sold"] = await ingest_leboncoin_sold(keyword, limits.get("leboncoin_listings", 50))  # Use same limit as listings

    # Vinted ingestion
    if "vinted" in sources:
        results["vinted_listings"] = await ingest_vinted_listings(keyword, limits.get("vinted_listings", 50))
        results["vinted_sold"] = await ingest_vinted_sold(keyword, limits.get("vinted_listings", 50))  # Use same limit as listings

    # Update metrics for all products that were updated
    with SessionLocal() as db:
        # Get unique product_ids that were processed
        product_ids = db.query(ListingObservation.product_id).distinct().all()
        for (product_id,) in product_ids:
            try:
                update_product_metrics(product_id)
            except Exception as e:
                logger.error(f"Error updating metrics for product {product_id}: {e}")

    logger.info(f"Full ingestion completed for keyword: {keyword}")
    return results

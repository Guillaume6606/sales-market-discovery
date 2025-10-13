from typing import List, Dict, Any, Iterable
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone, date, timedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func
from loguru import logger

from libs.common.models import (
    Category,
    ProductTemplate,
    ListingObservation,
    ProductDailyMetrics,
    MarketPriceNormal,
    Listing,
)
from libs.common.db import SessionLocal
from ingestion.connectors.ebay import fetch_ebay_sold, fetch_ebay_listings
from ingestion.connectors.leboncoin_api import (
    fetch_leboncoin_api_listings,
    fetch_leboncoin_api_sold,
)
from ingestion.constants import SUPPORTED_PROVIDERS
from ingestion.connectors.vinted import fetch_vinted_listings
from ingestion.pricing import pmn_from_prices


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
    providers: List[str]
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


def _matches_price(snapshot: ProductTemplateSnapshot, listing: Listing) -> bool:
    if listing.price is None:
        if snapshot.price_min is not None or snapshot.price_max is not None:
            return False
        return True
    if snapshot.price_min is not None and listing.price < snapshot.price_min:
        return False
    if snapshot.price_max is not None and listing.price > snapshot.price_max:
        return False
    return True


def _matches_brand(snapshot: ProductTemplateSnapshot, listing: Listing) -> bool:
    """
    Check if listing matches the product's brand.
    
    RELAXED MATCHING: Brand filtering is intentionally permissive because:
    1. _compose_search_term() already includes brand in the search query
    2. Search APIs (LeBonCoin, eBay, Vinted) already filter by that search term
    3. Over-filtering here causes false negatives (e.g., "PS4" listings when brand="Sony")
    
    We only apply strict brand matching if:
    - Brand is set AND
    - Brand is NOT in the search query (rare case: manual brand filter without search context)
    
    Example: search_query="PS4" + brand="Sony"
    - _compose_search_term() → "PS4 Sony" (brand added to search)
    - LeBonCoin returns PS4 listings (already brand-filtered by search)
    - We should NOT filter again, as "PS4 500GB" is a valid Sony PS4
    """
    if not snapshot.brand:
        return True
    
    brand_lower = snapshot.brand.lower()
    
    # CRITICAL FIX: If brand exists, _compose_search_term() adds it to the search.
    # The search API already filtered by brand, so trust those results.
    # This prevents over-filtering cases like "PS4" where titles don't say "Sony"
    composed_term = _compose_search_term(snapshot)
    if brand_lower in composed_term.lower():
        # Brand is in the search term, so search API already handled filtering
        logger.debug(f"Skipping brand filter - brand '{snapshot.brand}' already in search term")
        return True
    
    # Only apply strict brand matching if brand is NOT in search term (rare edge case)
    if listing.brand and listing.brand.lower() == brand_lower:
        return True

    if listing.title and brand_lower in listing.title.lower():
        return True

    return False


def _filter_listings(snapshot: ProductTemplateSnapshot, listings: Iterable[Listing]) -> List[Listing]:
    """
    Filter listings based on product template criteria.
    Logs filtering stats for debugging.
    """
    filtered: List[Listing] = []
    rejected_price = 0
    rejected_brand = 0
    
    for listing in listings:
        if not _matches_price(snapshot, listing):
            rejected_price += 1
            continue
        if not _matches_brand(snapshot, listing):
            rejected_brand += 1
            logger.debug(
                f"Listing '{listing.title[:50]}...' rejected: brand '{snapshot.brand}' "
                f"not found in title or brand field"
            )
            continue
        filtered.append(listing)
    
    if rejected_price > 0 or rejected_brand > 0:
        logger.info(
            f"Filtered {len(listings)} listings: {len(filtered)} kept, "
            f"{rejected_price} rejected (price), {rejected_brand} rejected (brand)"
        )
    
    return filtered


def _dedupe_listings(listings: Iterable[Listing]) -> List[Listing]:
    seen: set[tuple[str, str]] = set()
    deduped: List[Listing] = []
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
) -> None:
    listing_source = listing.source
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
        )
        db.add(observation)


def _persist_listings(
    product_id: str,
    listings: List[Listing],
    *,
    force_is_sold: bool | None = None,
) -> int:
    if not listings:
        return 0

    processed_count = 0
    with SessionLocal() as db:
        product = (
            db.query(ProductTemplate)
            .filter(ProductTemplate.product_id == product_id)
            .first()
        )
        if not product:
            logger.warning(f"Product template {product_id} no longer exists; skipping persistence")
            return 0

        for listing in listings:
            try:
                _upsert_listing(db, product, listing, force_is_sold=force_is_sold)
                processed_count += 1
            except Exception as exc:
                logger.error(
                    f"Failed to persist listing {listing.listing_id} for product {product_id}: {exc}"
                )

        product.last_ingested_at = datetime.now(timezone.utc)
        db.commit()

    return processed_count


async def ingest_ebay_sold(product_id: str, limit: int = 50) -> Dict[str, Any]:
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

    try:
        # fetch_ebay_sold now returns List[Listing] directly
        listings = await fetch_ebay_sold(_compose_search_term(snapshot), limit)
        
        if not listings:
            logger.info(f"No eBay sold items found for product {snapshot.product_id}")
            return {"status": "success", "count": 0, "message": "No items found"}

        filtered = _filter_listings(snapshot, _dedupe_listings(listings))
        processed = _persist_listings(snapshot.product_id, filtered, force_is_sold=True)

        if processed:
            logger.info(f"Ingested {processed} eBay sold listings for product {snapshot.product_id}")
            return {"status": "success", "count": processed}

        logger.warning(f"No eBay sold listings matched filters for product {snapshot.product_id}")
        return {"status": "no_data", "count": 0}

    except Exception as exc:
        logger.error(f"Error in eBay sold ingestion for product {snapshot.product_id}: {exc}")
        return {"status": "error", "error": str(exc)}

async def ingest_ebay_listings(product_id: str, limit: int = 50) -> Dict[str, Any]:
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
        # fetch_ebay_listings now returns List[Listing] directly
        listings = await fetch_ebay_listings(_compose_search_term(snapshot), limit)
        
        if not listings:
            logger.info(f"No eBay listings found for product {snapshot.product_id}")
            return {"status": "success", "count": 0, "message": "No items found"}

        filtered = _filter_listings(snapshot, _dedupe_listings(listings))
        processed = _persist_listings(snapshot.product_id, filtered, force_is_sold=False)

        if processed:
            logger.info(
                f"Ingested {processed} eBay active listings for product {snapshot.product_id}"
            )
            return {"status": "success", "count": processed}

        logger.warning(f"No eBay listings matched filters for product {snapshot.product_id}")
        return {"status": "no_data", "count": 0}

    except Exception as exc:
        logger.error(f"Error in eBay listings ingestion for product {snapshot.product_id}: {exc}")
        return {"status": "error", "error": str(exc)}

async def ingest_leboncoin_listings(product_id: str, limit: int = 50) -> Dict[str, Any]:
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting LeBonCoin listings ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    try:
        listings = await fetch_leboncoin_api_listings(_compose_search_term(snapshot), limit)
        filtered = _filter_listings(snapshot, _dedupe_listings(listings))
        processed = _persist_listings(snapshot.product_id, filtered, force_is_sold=False)

        if processed:
            logger.info(
                f"Ingested {processed} LeBonCoin listings for product {snapshot.product_id}"
            )
            return {"status": "success", "count": processed}

        logger.warning(
            f"No LeBonCoin listings matched filters for product {snapshot.product_id}"
        )
        return {"status": "no_data", "count": 0}

    except Exception as exc:
        logger.error(
            f"Error in LeBonCoin listings ingestion for product {snapshot.product_id}: {exc}"
        )
        return {"status": "error", "error": str(exc)}

async def ingest_leboncoin_sold(product_id: str, limit: int = 50) -> Dict[str, Any]:
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting LeBonCoin 'sold' ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    try:
        listings = await fetch_leboncoin_api_sold(_compose_search_term(snapshot), limit)
        filtered = _filter_listings(snapshot, _dedupe_listings(listings))
        processed = _persist_listings(snapshot.product_id, filtered, force_is_sold=True)

        if processed:
            logger.info(
                f"Ingested {processed} LeBonCoin 'sold' listings for product {snapshot.product_id}"
            )
            return {"status": "success", "count": processed}

        logger.warning(
            f"No LeBonCoin 'sold' listings matched filters for product {snapshot.product_id}"
        )
        return {"status": "no_data", "count": 0}

    except Exception as exc:
        logger.error(
            f"Error in LeBonCoin 'sold' ingestion for product {snapshot.product_id}: {exc}"
        )
        return {"status": "error", "error": str(exc)}

async def ingest_vinted_listings(product_id: str, limit: int = 50) -> Dict[str, Any]:
    snapshot = _load_product_snapshot(product_id)
    if not snapshot:
        return {"status": "error", "error": "Product template not found or inactive"}

    logger.info(
        f"Starting Vinted listings ingestion for product '{snapshot.name}' ({snapshot.product_id})"
    )

    try:
        listings = await fetch_vinted_listings(_compose_search_term(snapshot), limit)
        filtered = _filter_listings(snapshot, _dedupe_listings(listings))
        processed = _persist_listings(snapshot.product_id, filtered, force_is_sold=False)

        if processed:
            logger.info(
                f"Ingested {processed} Vinted listings for product {snapshot.product_id}"
            )
            return {"status": "success", "count": processed}

        logger.warning(
            f"No Vinted listings matched filters for product {snapshot.product_id}"
        )
        return {"status": "no_data", "count": 0}

    except Exception as exc:
        logger.error(
            f"Error in Vinted listings ingestion for product {snapshot.product_id}: {exc}"
        )
        return {"status": "error", "error": str(exc)}



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

async def run_full_ingestion(
    product_id: str,
    limits: Dict[str, int] | None = None,
    sources: List[str] | None = None,
) -> Dict[str, Any]:
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

    results: Dict[str, Any] = {
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
        results.setdefault("warnings", []).append(
            f"metrics_update_failed: {snapshot.product_id}"
        )

    logger.info(
        f"Full ingestion completed for product '{snapshot.name}' ({snapshot.product_id})"
    )
    results["status"] = "success"
    return results

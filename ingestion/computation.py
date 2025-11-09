"""
Computation Engine for Market Discovery Platform

This module implements the core computation algorithms:
1. PMN Engine - Price of Market Normal calculation and persistence
2. Liquidity Engine - Market velocity and depth analysis
3. Margin Estimator - Profit margin calculation with fees
4. Opportunity Scoring - Composite scoring for listing attractiveness
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Tuple
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from libs.common.db import SessionLocal
from libs.common.models import (
    ProductTemplate,
    ListingObservation,
    MarketPriceNormal,
    ProductDailyMetrics
)
from ingestion.pricing import pmn_from_prices

logger = logging.getLogger(__name__)

# Platform-specific fee configuration
PLATFORM_FEES = {
    "ebay": {
        "commission": 0.129,  # 12.9% eBay final value fee
        "payment": 0.030,     # ~3% payment processing
        "name": "eBay"
    },
    "leboncoin": {
        "commission": 0.05,   # 5% LeBonCoin commission
        "payment": 0.03,      # 3% payment processing
        "name": "LeBonCoin"
    },
    "vinted": {
        "commission": 0.05,   # 5% Vinted buyer protection
        "payment": 0.03,      # 3% payment processing
        "name": "Vinted"
    }
}

# Default fees if source not recognized
DEFAULT_FEES = {
    "commission": 0.10,
    "payment": 0.03,
    "name": "Unknown"
}


# ============================================================================
# PMN ENGINE - Price of Market Normal
# ============================================================================

def compute_pmn_for_product(product_id: str, db: Session | None = None) -> Dict[str, Any]:
    """
    Compute and persist PMN (Price of Market Normal) for a product.
    
    Uses a hybrid approach:
    1. Primary: Sold items from last 90 days (most reliable)
    2. Fallback: Active listings if sold items < 10
    
    Args:
        product_id: UUID of the product template
        db: Optional database session (creates new if not provided)
        
    Returns:
        Dict with computation status and results
    """
    should_close = db is None
    if db is None:
        db = SessionLocal()
    
    try:
        # Check if product exists
        product = db.query(ProductTemplate).filter(
            ProductTemplate.product_id == product_id
        ).first()
        
        if not product:
            return {
                "status": "error",
                "error": "product_not_found",
                "product_id": product_id
            }
        
        # Fetch sold items from last 90 days
        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
        
        sold_items = db.query(
            ListingObservation.price,
            ListingObservation.observed_at
        ).filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold == True,
            ListingObservation.price.isnot(None),
            ListingObservation.observed_at >= ninety_days_ago
        ).all()
        
        prices = [float(item.price) for item in sold_items]
        timestamps = [item.observed_at for item in sold_items]
        
        data_source = "sold_items_90d"
        
        # Fallback to active listings if insufficient sold data
        if len(prices) < 10:
            logger.info(f"Product {product_id}: Only {len(prices)} sold items, including active listings")
            
            active_items = db.query(
                ListingObservation.price,
                ListingObservation.observed_at
            ).filter(
                ListingObservation.product_id == product_id,
                ListingObservation.is_sold == False,
                ListingObservation.price.isnot(None),
                ListingObservation.observed_at >= ninety_days_ago
            ).all()
            
            active_prices = [float(item.price) for item in active_items]
            active_timestamps = [item.observed_at for item in active_items]
            
            prices.extend(active_prices)
            timestamps.extend(active_timestamps)
            
            data_source = f"sold_{len(sold_items)}_active_{len(active_items)}"
        
        # Check minimum data requirement
        if len(prices) < 3:
            logger.warning(f"Product {product_id}: Insufficient data for PMN ({len(prices)} prices)")
            return {
                "status": "insufficient_data",
                "product_id": product_id,
                "price_count": len(prices),
                "min_required": 3
            }
        
        # Calculate PMN with time weighting for larger datasets
        time_weighted = len(prices) >= 20
        pmn_result = pmn_from_prices(prices, timestamps, time_weighted=time_weighted)
        
        # Add data source to methodology
        pmn_result["methodology"]["data_source"] = data_source
        
        # Persist to database
        existing_pmn = db.query(MarketPriceNormal).filter(
            MarketPriceNormal.product_id == product_id
        ).first()
        
        if existing_pmn:
            # Update existing record
            existing_pmn.pmn = pmn_result["pmn"]
            existing_pmn.pmn_low = pmn_result["pmn_low"]
            existing_pmn.pmn_high = pmn_result["pmn_high"]
            existing_pmn.last_computed_at = datetime.now(timezone.utc)
            existing_pmn.methodology = pmn_result["methodology"]
        else:
            # Create new record
            new_pmn = MarketPriceNormal(
                product_id=product_id,
                pmn=pmn_result["pmn"],
                pmn_low=pmn_result["pmn_low"],
                pmn_high=pmn_result["pmn_high"],
                last_computed_at=datetime.now(timezone.utc),
                methodology=pmn_result["methodology"]
            )
            db.add(new_pmn)
        
        db.commit()
        
        logger.info(
            f"PMN computed for product {product_id}: "
            f"€{pmn_result['pmn']:.2f} (±{pmn_result['pmn_high'] - pmn_result['pmn']:.2f}), "
            f"n={pmn_result['n']}, method={pmn_result['methodology']['method']}"
        )
        
        return {
            "status": "success",
            "product_id": product_id,
            "pmn": pmn_result["pmn"],
            "pmn_low": pmn_result["pmn_low"],
            "pmn_high": pmn_result["pmn_high"],
            "sample_size": pmn_result["n"],
            "methodology": pmn_result["methodology"]
        }
        
    except Exception as e:
        logger.error(f"Error computing PMN for product {product_id}: {e}", exc_info=True)
        if db:
            db.rollback()
        return {
            "status": "error",
            "error": str(e),
            "product_id": product_id
        }
    finally:
        if should_close and db:
            db.close()


# ============================================================================
# LIQUIDITY ENGINE - Market Velocity & Depth
# ============================================================================

def compute_liquidity_score(product_id: str, db: Session | None = None) -> Dict[str, Any]:
    """
    Calculate enhanced liquidity score (0-100) based on:
    1. Sales velocity (items sold per day) - 50 points max
    2. Market depth (active listings count) - 25 points max
    3. Time-to-sell estimate - 25 points max
    
    Args:
        product_id: UUID of the product template
        db: Optional database session
        
    Returns:
        Dict with liquidity score and breakdown
    """
    should_close = db is None
    if db is None:
        db = SessionLocal()
    
    try:
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        
        # 1. Sales velocity (sold items in last 30 days)
        sold_count_30d = db.query(func.count(ListingObservation.obs_id)).filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold == True,
            ListingObservation.observed_at >= thirty_days_ago
        ).scalar() or 0
        
        sold_count_7d = db.query(func.count(ListingObservation.obs_id)).filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold == True,
            ListingObservation.observed_at >= seven_days_ago
        ).scalar() or 0
        
        # Velocity score: 1 sale/day = 50 points, scaled
        velocity_score = min((sold_count_30d / 30.0) * 50.0, 50.0)
        
        # 2. Market depth (active listings)
        active_count = db.query(func.count(ListingObservation.obs_id)).filter(
            ListingObservation.product_id == product_id,
            ListingObservation.is_sold == False
        ).scalar() or 0
        
        # Depth score: 20 active listings = 25 points, scaled
        depth_score = min((active_count / 20.0) * 25.0, 25.0)
        
        # 3. Time-to-sell estimate (simplified for now)
        # Higher sales rate = faster selling = higher score
        if sold_count_30d > 0:
            # If selling consistently, award points
            freshness_score = min((sold_count_30d / 15.0) * 25.0, 25.0)
        else:
            freshness_score = 0.0
        
        # Calculate total liquidity score
        liquidity_score = velocity_score + depth_score + freshness_score
        
        # Calculate average time to sell (hours) - placeholder for future enhancement
        avg_time_to_sell = None
        if sold_count_30d > 0:
            # Simple estimate: 30 days / number of sales = avg days between sales
            avg_days_between = 30.0 / sold_count_30d
            avg_time_to_sell = int(avg_days_between * 24)  # Convert to hours
        
        return {
            "liquidity_score": round(liquidity_score, 2),
            "sold_count_30d": sold_count_30d,
            "sold_count_7d": sold_count_7d,
            "active_listings_count": active_count,
            "avg_time_to_sell_hours": avg_time_to_sell,
            "breakdown": {
                "velocity_score": round(velocity_score, 2),
                "depth_score": round(depth_score, 2),
                "freshness_score": round(freshness_score, 2)
            }
        }
        
    except Exception as e:
        logger.error(f"Error computing liquidity for product {product_id}: {e}")
        return {
            "liquidity_score": 0.0,
            "error": str(e)
        }
    finally:
        if should_close and db:
            db.close()


# ============================================================================
# MARGIN ESTIMATOR - Profit Calculation with Fees
# ============================================================================

def estimate_margin(
    listing_price: float,
    pmn: float,
    shipping_cost: float | None,
    source: str
) -> Dict[str, Any]:
    """
    Estimate profit margin for a listing including platform fees.
    
    Args:
        listing_price: Current listing price
        pmn: Predicted Market Normal (expected resale price)
        shipping_cost: Shipping cost (if applicable)
        source: Platform source (ebay, leboncoin, vinted)
        
    Returns:
        Dict with margin analysis and risk assessment
    """
    if listing_price is None or pmn is None or listing_price <= 0 or pmn <= 0:
        return {
            "gross_margin": None,
            "gross_margin_pct": None,
            "net_margin": None,
            "net_margin_pct": None,
            "risk_level": "unknown"
        }
    
    # Get platform fees
    fees_config = PLATFORM_FEES.get(source.lower(), DEFAULT_FEES)
    
    # Calculate gross margin (before fees)
    gross_margin = pmn - listing_price
    gross_margin_pct = (gross_margin / listing_price) * 100 if listing_price > 0 else 0
    
    # Calculate fees on the RESALE price (PMN)
    commission_fee = pmn * fees_config["commission"]
    payment_fee = pmn * fees_config["payment"]
    shipping_fee = shipping_cost or 0.0
    
    total_fees = commission_fee + payment_fee + shipping_fee
    
    # Calculate net margin (after fees)
    net_margin = gross_margin - total_fees
    net_margin_pct = (net_margin / listing_price) * 100 if listing_price > 0 else 0
    
    # Risk assessment based on margin
    if net_margin_pct >= 20:
        risk_level = "low"
        risk_description = "Strong margin, low risk"
    elif net_margin_pct >= 10:
        risk_level = "medium"
        risk_description = "Moderate margin, some risk"
    elif net_margin_pct >= 0:
        risk_level = "high"
        risk_description = "Thin margin, high risk"
    else:
        risk_level = "very_high"
        risk_description = "Negative margin, avoid"
    
    return {
        "gross_margin": round(gross_margin, 2),
        "gross_margin_pct": round(gross_margin_pct, 2),
        "net_margin": round(net_margin, 2),
        "net_margin_pct": round(net_margin_pct, 2),
        "fees": {
            "platform_fee": round(commission_fee, 2),
            "payment_fee": round(payment_fee, 2),
            "shipping": round(shipping_fee, 2),
            "total_fees": round(total_fees, 2)
        },
        "breakdown": {
            "purchase_price": round(listing_price, 2),
            "expected_resale": round(pmn, 2),
            "total_costs": round(listing_price + total_fees, 2)
        },
        "risk_level": risk_level,
        "risk_description": risk_description
    }


# ============================================================================
# OPPORTUNITY SCORING - Composite Listing Attractiveness
# ============================================================================

def compute_opportunity_score(
    listing: ListingObservation,
    product_metrics: ProductDailyMetrics | None,
    pmn_data: MarketPriceNormal | None
) -> Dict[str, Any]:
    """
    Calculate composite opportunity score (0-100) for a listing.
    
    Scoring breakdown:
    - Margin score (40 points): How good is the deal?
    - Liquidity score (30 points): How fast will it sell?
    - Risk score (30 points): How reliable is the deal?
    
    Args:
        listing: ListingObservation record
        product_metrics: Optional ProductDailyMetrics record
        pmn_data: Optional MarketPriceNormal record
        
    Returns:
        Dict with opportunity score and detailed breakdown
    """
    if not listing.price or not pmn_data or not pmn_data.pmn:
        return {
            "opportunity_score": 0.0,
            "recommendation": "insufficient_data",
            "reason": "Missing price or PMN data"
        }
    
    listing_price = float(listing.price)
    pmn = float(pmn_data.pmn)
    
    # 1. MARGIN SCORE (40 points max)
    # Calculate net margin percentage
    margin_data = estimate_margin(
        listing_price,
        pmn,
        float(listing.shipping_cost) if listing.shipping_cost else None,
        listing.source
    )
    
    net_margin_pct = margin_data.get("net_margin_pct", 0)
    
    # Score: 30% margin = 40 points, scaled linearly
    if net_margin_pct >= 30:
        margin_score = 40.0
    elif net_margin_pct > 0:
        margin_score = (net_margin_pct / 30.0) * 40.0
    else:
        margin_score = 0.0
    
    # 2. LIQUIDITY SCORE (30 points max)
    # Use computed liquidity score from metrics (0-100) and scale to 30 points
    if product_metrics and product_metrics.liquidity_score:
        liquidity_raw = float(product_metrics.liquidity_score)
        liquidity_score = (liquidity_raw / 100.0) * 30.0
    else:
        # Fallback: assume medium liquidity
        liquidity_score = 15.0
    
    # 3. RISK SCORE (30 points max)
    risk_score = 15.0  # Start with neutral score
    
    # Seller rating factor (+10 points for high rating)
    if listing.seller_rating:
        rating = float(listing.seller_rating)
        if rating >= 4.5:
            risk_score += 10
        elif rating >= 4.0:
            risk_score += 7
        elif rating >= 3.5:
            risk_score += 4
        elif rating >= 3.0:
            risk_score += 2
    else:
        # No rating = moderate risk
        risk_score += 5
    
    # Condition factor (+5 points for good condition)
    if listing.condition:
        condition_lower = listing.condition.lower()
        if any(term in condition_lower for term in ["new", "neuf", "nouveau"]):
            risk_score += 5
        elif any(term in condition_lower for term in ["like new", "excellent", "très bon"]):
            risk_score += 4
        elif any(term in condition_lower for term in ["good", "bon"]):
            risk_score += 3
        elif any(term in condition_lower for term in ["fair", "correct"]):
            risk_score += 2
    
    # Price confidence factor (-5 if price too far below PMN)
    price_deviation_pct = ((pmn - listing_price) / pmn) * 100 if pmn > 0 else 0
    if price_deviation_pct > 50:
        # Too good to be true? Reduce risk score
        risk_score -= 5
    
    # Ensure risk score is within bounds
    risk_score = max(0, min(30, risk_score))
    
    # TOTAL OPPORTUNITY SCORE
    opportunity_score = margin_score + liquidity_score + risk_score
    
    # RECOMMENDATION
    if opportunity_score >= 75:
        recommendation = "strong_buy"
        rec_description = "Excellent opportunity - high margin, good liquidity, low risk"
    elif opportunity_score >= 60:
        recommendation = "good_buy"
        rec_description = "Good opportunity - solid fundamentals"
    elif opportunity_score >= 40:
        recommendation = "fair"
        rec_description = "Fair opportunity - consider carefully"
    else:
        recommendation = "pass"
        rec_description = "Poor opportunity - low score"
    
    return {
        "opportunity_score": round(opportunity_score, 2),
        "breakdown": {
            "margin_score": round(margin_score, 2),
            "liquidity_score": round(liquidity_score, 2),
            "risk_score": round(risk_score, 2)
        },
        "recommendation": recommendation,
        "description": rec_description,
        "margin_analysis": margin_data,
        "metrics": {
            "net_margin_pct": net_margin_pct,
            "price_deviation_pct": round(price_deviation_pct, 2) if price_deviation_pct else None,
            "seller_rating": float(listing.seller_rating) if listing.seller_rating else None
        }
    }


# ============================================================================
# BATCH COMPUTATION - Process Multiple Products
# ============================================================================

def compute_all_product_metrics(
    product_ids: List[str] | None = None,
    db: Session | None = None
) -> Dict[str, Any]:
    """
    Compute PMN and liquidity metrics for multiple products.
    
    Args:
        product_ids: Optional list of product IDs (if None, processes all active products)
        db: Optional database session
        
    Returns:
        Dict with computation statistics
    """
    should_close = db is None
    if db is None:
        db = SessionLocal()
    
    try:
        # Get product IDs if not provided
        if product_ids is None:
            products = db.query(ProductTemplate.product_id).filter(
                ProductTemplate.is_active == True
            ).all()
            product_ids = [str(p.product_id) for p in products]
        
        logger.info(f"Starting batch computation for {len(product_ids)} products")
        
        results = {
            "total": len(product_ids),
            "pmn_computed": 0,
            "pmn_insufficient_data": 0,
            "pmn_errors": 0,
            "metrics_updated": 0,
            "metrics_errors": 0
        }
        
        for product_id in product_ids:
            # Compute PMN
            pmn_result = compute_pmn_for_product(product_id, db)
            
            if pmn_result["status"] == "success":
                results["pmn_computed"] += 1
            elif pmn_result["status"] == "insufficient_data":
                results["pmn_insufficient_data"] += 1
            else:
                results["pmn_errors"] += 1
            
            # Compute liquidity and update metrics
            try:
                liquidity_data = compute_liquidity_score(product_id, db)
                
                # Update or create daily metrics
                from datetime import date
                existing_metrics = db.query(ProductDailyMetrics).filter(
                    and_(
                        ProductDailyMetrics.product_id == product_id,
                        ProductDailyMetrics.date == date.today()
                    )
                ).first()
                
                if existing_metrics:
                    existing_metrics.liquidity_score = liquidity_data["liquidity_score"]
                    existing_metrics.sold_count_30d = liquidity_data["sold_count_30d"]
                    existing_metrics.sold_count_7d = liquidity_data["sold_count_7d"]
                else:
                    new_metrics = ProductDailyMetrics(
                        product_id=product_id,
                        date=date.today(),
                        liquidity_score=liquidity_data["liquidity_score"],
                        sold_count_30d=liquidity_data["sold_count_30d"],
                        sold_count_7d=liquidity_data["sold_count_7d"]
                    )
                    db.add(new_metrics)
                
                db.commit()
                results["metrics_updated"] += 1
                
            except Exception as e:
                logger.error(f"Error updating metrics for product {product_id}: {e}")
                results["metrics_errors"] += 1
                db.rollback()
        
        logger.info(f"Batch computation completed: {results}")
        return results
        
    except Exception as e:
        logger.error(f"Error in batch computation: {e}", exc_info=True)
        return {"error": str(e)}
    finally:
        if should_close and db:
            db.close()



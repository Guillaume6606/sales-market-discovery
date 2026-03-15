"""
Alert rule evaluation engine for triggering Telegram notifications.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from libs.common.db import SessionLocal
from libs.common.models import (
    AlertEvent,
    AlertRule,
    ListingObservation,
    MarketPriceNormal,
    ProductDailyMetrics,
    ProductTemplate,
)
from libs.common.settings import settings
from libs.common.telegram_service import send_opportunity_alert


def _decimal_to_float(value: Decimal | float | None) -> float | None:
    """Convert Decimal to float."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def evaluate_alert_rules(
    listing: ListingObservation,
    product_template: ProductTemplate,
    pmn_data: MarketPriceNormal | None = None,
    metrics: ProductDailyMetrics | None = None,
    db: Session | None = None,
) -> list[AlertRule]:
    """
    Evaluate all active alert rules against a listing.

    Args:
        listing: The listing observation to evaluate
        product_template: Product template for the listing
        pmn_data: PMN data for the product (optional)
        metrics: Daily metrics for the product (optional)
        db: Database session (optional, will create if not provided)

    Returns:
        List of AlertRule objects that match the listing
    """
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False

    try:
        # Get all active alert rules
        rules = db.query(AlertRule).filter(AlertRule.is_active == True).all()

        matching_rules = []

        for rule in rules:
            if _rule_matches(rule, listing, product_template, pmn_data, metrics):
                matching_rules.append(rule)

        return matching_rules

    finally:
        if should_close:
            db.close()


def _rule_matches(
    rule: AlertRule,
    listing: ListingObservation,
    product_template: ProductTemplate,
    pmn_data: MarketPriceNormal | None,
    metrics: ProductDailyMetrics | None,
) -> bool:
    """
    Check if an alert rule matches a listing.

    Returns:
        True if rule matches, False otherwise
    """
    # Check product filter (JSON criteria)
    if rule.product_filter:
        # Simple product filter matching (can be extended)
        product_filter = rule.product_filter
        if isinstance(product_filter, dict):
            # Check category
            if "category_id" in product_filter:
                if str(product_template.category_id) != str(product_filter["category_id"]):
                    return False

            # Check brand
            if "brand" in product_filter:
                if product_template.brand != product_filter["brand"]:
                    return False

    # Skip if listing is sold
    if listing.is_sold:
        return False

    # Check price and margin thresholds
    if pmn_data and pmn_data.pmn and listing.price:
        pmn_value = _decimal_to_float(pmn_data.pmn)
        listing_price = _decimal_to_float(listing.price)

        if pmn_value and listing_price:
            # Calculate margin percentage
            margin_pct = ((listing_price - pmn_value) / pmn_value) * 100

            # Check threshold_pct (margin % below PMN)
            if rule.threshold_pct is not None:
                if margin_pct > rule.threshold_pct:
                    return False

            # Check min_margin_abs (absolute margin)
            if rule.min_margin_abs is not None:
                margin_abs = pmn_value - listing_price
                if margin_abs < _decimal_to_float(rule.min_margin_abs):
                    return False

    # Check liquidity score
    if rule.min_liquidity_score is not None and metrics:
        liquidity = _decimal_to_float(metrics.liquidity_score)
        min_liquidity = _decimal_to_float(rule.min_liquidity_score)
        if liquidity is None or (min_liquidity and liquidity < min_liquidity):
            return False

    # Check seller rating
    if rule.min_seller_rating is not None:
        seller_rating = _decimal_to_float(listing.seller_rating)
        min_rating = _decimal_to_float(rule.min_seller_rating)
        if seller_rating is None or (min_rating and seller_rating < min_rating):
            return False

    return True


def _check_duplicate_alert(
    db: Session,
    rule_id: str,
    obs_id: int,
) -> bool:
    """
    Check if an alert has already been sent for this rule and listing.

    Returns:
        True if duplicate exists, False otherwise
    """
    existing = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.rule_id == rule_id,
            AlertEvent.obs_id == obs_id,
            AlertEvent.suppressed == False,
        )
        .first()
    )

    return existing is not None


async def trigger_alerts(
    opportunities: list[dict[str, Any]],
    db: Session | None = None,
) -> list[AlertEvent]:
    """
    Evaluate alert rules and send Telegram alerts for matching opportunities.

    Args:
        opportunities: List of dicts with keys:
            - listing: ListingObservation object
            - product_template: ProductTemplate object
            - pmn_data: MarketPriceNormal object (optional)
            - metrics: ProductDailyMetrics object (optional)
        db: Database session (optional)

    Returns:
        List of AlertEvent objects created
    """
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False

    created_events = []

    try:
        for opp in opportunities:
            listing = opp.get("listing")
            product_template = opp.get("product_template")
            pmn_data = opp.get("pmn_data")
            metrics = opp.get("metrics")

            if not listing or not product_template:
                continue

            # Suppress alerts for low-confidence PMN
            if pmn_data and pmn_data.confidence is not None:
                conf = float(pmn_data.confidence)
                if conf < settings.min_pmn_confidence:
                    suppressed_event = AlertEvent(
                        product_id=product_template.product_id,
                        obs_id=listing.obs_id,
                        sent_at=datetime.now(UTC),
                        delivery={
                            "suppressed_reason": "low_pmn_confidence",
                            "confidence": conf,
                        },
                        suppressed=True,
                    )
                    db.add(suppressed_event)
                    created_events.append(suppressed_event)
                    continue

            # Evaluate rules
            matching_rules = evaluate_alert_rules(listing, product_template, pmn_data, metrics, db)

            for rule in matching_rules:
                # Check for duplicates
                if _check_duplicate_alert(db, str(rule.rule_id), listing.obs_id):
                    logger.debug(
                        f"Skipping duplicate alert for rule {rule.rule_id} and listing {listing.obs_id}"
                    )
                    continue

                # Calculate opportunity details
                margin_pct = None
                margin_abs = None
                pmn_value = None

                if pmn_data and pmn_data.pmn and listing.price:
                    pmn_value = _decimal_to_float(pmn_data.pmn)
                    listing_price = _decimal_to_float(listing.price)
                    if pmn_value and listing_price:
                        margin_pct = ((listing_price - pmn_value) / pmn_value) * 100
                        margin_abs = pmn_value - listing_price

                # Prepare opportunity dict
                opportunity_dict = {
                    "margin_pct": margin_pct,
                    "margin_abs": margin_abs,
                    "pmn": pmn_value,
                }

                # Prepare listing dict
                listing_dict = {
                    "listing_id": listing.listing_id,
                    "title": listing.title,
                    "price": _decimal_to_float(listing.price),
                    "url": listing.url,
                    "condition": listing.condition,
                    "seller_rating": _decimal_to_float(listing.seller_rating),
                }

                # Prepare product template dict
                product_dict = {
                    "product_id": str(product_template.product_id),
                    "name": product_template.name,
                    "brand": product_template.brand,
                    "description": product_template.description,
                }

                # Create alert event first (to get alert_id for inline keyboard)
                alert_event = AlertEvent(
                    rule_id=rule.rule_id,
                    product_id=product_template.product_id,
                    obs_id=listing.obs_id,
                    sent_at=datetime.now(UTC),
                    delivery=None,
                    suppressed=False,
                )
                db.add(alert_event)
                db.flush()  # Get alert_id

                # Send Telegram alert with alert_id for inline keyboard
                screenshot_path = listing.screenshot_path
                pmn_conf = (
                    float(pmn_data.confidence)
                    if pmn_data and pmn_data.confidence is not None
                    else None
                )
                send_result = await send_opportunity_alert(
                    opportunity_dict,
                    listing_dict,
                    product_dict,
                    screenshot_path=screenshot_path,
                    pmn_confidence=pmn_conf,
                    alert_id=alert_event.alert_id,
                )

                # Update delivery result and commit immediately so the
                # alert_id referenced in the Telegram callback_data is
                # persisted even if a later iteration fails.
                alert_event.delivery = send_result
                db.commit()
                created_events.append(alert_event)

                logger.info(
                    f"Triggered alert for rule {rule.name} (ID: {rule.rule_id}) "
                    f"on listing {listing.obs_id}"
                )
        return created_events

    except Exception as e:
        logger.error(f"Error triggering alerts: {e}", exc_info=True)
        db.rollback()
        return []
    finally:
        if should_close:
            db.close()

"""Verified opportunity eligibility and a transactional Telegram delivery outbox."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert
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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _rule_matches(
    rule: AlertRule,
    listing: ListingObservation,
    product_template: ProductTemplate,
    pmn_data: MarketPriceNormal | None,
    metrics: ProductDailyMetrics | None,
    *,
    valuation: dict[str, Any] | None = None,
) -> bool:
    if rule.channels is not None and "telegram" not in rule.channels:
        return False
    if not valuation or not valuation.get("eligible"):
        return False
    if listing.is_sold or listing.is_stale or not product_template.is_active:
        return False
    if listing.currency != "EUR" or listing.price is None or listing.price <= 0:
        return False
    if not listing.last_seen_at or _aware(listing.last_seen_at) < datetime.now(UTC) - timedelta(
        minutes=settings.alert_freshness_minutes
    ):
        return False
    filters = rule.product_filter or {}
    if "category_id" in filters and str(product_template.category_id) != str(
        filters["category_id"]
    ):
        return False
    if "brand" in filters and product_template.brand != filters["brand"]:
        return False
    if "product_id" in filters and str(product_template.product_id) != str(filters["product_id"]):
        return False
    exit_price = Decimal(str(valuation["estimated_sale_price_eur"]))
    contribution = Decimal(str(valuation["contribution_eur"]))
    if exit_price <= 0 or contribution <= 0:
        return False
    discount = (exit_price - Decimal(str(listing.price))) / exit_price * 100
    if rule.threshold_pct is not None and discount < abs(Decimal(str(rule.threshold_pct))):
        return False
    if rule.min_margin_abs is not None and contribution < rule.min_margin_abs:
        return False
    if rule.min_liquidity_score is not None:
        if (
            metrics is None
            or metrics.liquidity_score is None
            or metrics.liquidity_score < rule.min_liquidity_score
        ):
            return False
    if rule.min_seller_rating is not None:
        # eBay feedbackScore is a count, not a star rating.
        if (
            listing.source == "ebay"
            or listing.seller_rating is None
            or listing.seller_rating < rule.min_seller_rating
        ):
            return False
    return True


def apply_capital_gate(valuation: dict[str, Any], committed: Decimal) -> dict[str, Any]:
    result = dict(valuation)
    reasons = list(result.get("reasons", []))
    budget = settings.working_capital_eur
    available = max(Decimal("0"), budget - committed) if budget is not None else None
    result["capital_committed_eur"] = committed
    result["capital_available_eur"] = available
    if budget is None:
        reasons.append("working_capital_unconfigured")
    elif (
        result.get("acquisition_cost_eur") is not None
        and Decimal(str(result["acquisition_cost_eur"])) > available
    ):
        reasons.append("insufficient_available_capital")
    result["reasons"] = reasons
    result["eligible"] = bool(result.get("eligible")) and not reasons
    return result


def _alert_valuation(db: Session, listing: ListingObservation) -> dict[str, Any]:
    from ingestion.valuation import evaluate_valuation
    from libs.common.trades import query_capital_committed

    return apply_capital_gate(evaluate_valuation(db, listing), query_capital_committed(db))


def evaluate_alert_rules(
    listing: ListingObservation,
    product_template: ProductTemplate,
    pmn_data: MarketPriceNormal | None = None,
    metrics: ProductDailyMetrics | None = None,
    db: Session | None = None,
) -> list[AlertRule]:

    owned = db is None
    db = db or SessionLocal()
    try:
        valuation = _alert_valuation(db, listing)
        return [
            rule
            for rule in db.query(AlertRule).filter(AlertRule.is_active.is_(True)).all()
            if _rule_matches(
                rule, listing, product_template, pmn_data, metrics, valuation=valuation
            )
        ]
    finally:
        if owned:
            db.close()


def _check_duplicate_alert(db: Session, rule_id: str, obs_id: int) -> bool:
    return (
        db.query(AlertEvent.alert_id)
        .filter(
            AlertEvent.rule_id == rule_id,
            AlertEvent.obs_id == obs_id,
            AlertEvent.suppressed.is_(False),
            AlertEvent.delivery_status.in_(["sent", "legacy"]),
        )
        .first()
        is not None
    )


def _snapshot(valuation: dict[str, Any]) -> dict[str, Any]:
    import json

    return json.loads(json.dumps(valuation, default=str))


async def trigger_alerts(
    opportunities: list[dict[str, Any]], db: Session | None = None
) -> list[AlertEvent]:

    owned = db is None
    db = db or SessionLocal()
    ids = []
    try:
        rules = db.query(AlertRule).filter(AlertRule.is_active.is_(True)).all()
        for opportunity in opportunities:
            listing = opportunity.get("listing")
            product = opportunity.get("product_template")
            if listing is None or product is None:
                continue
            valuation = _alert_valuation(db, listing)
            for rule in rules:
                if not _rule_matches(
                    rule, listing, product, None, opportunity.get("metrics"), valuation=valuation
                ):
                    continue
                if _check_duplicate_alert(db, str(rule.rule_id), listing.obs_id):
                    continue
                stmt = (
                    insert(AlertEvent)
                    .values(
                        rule_id=rule.rule_id,
                        product_id=product.product_id,
                        obs_id=listing.obs_id,
                        idempotency_key=f"{rule.rule_id}:{listing.obs_id}",
                        delivery_status="pending",
                        delivery_attempts=0,
                        suppressed=False,
                        next_attempt_at=datetime.now(UTC),
                        delivery={"valuation": _snapshot(valuation)},
                    )
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                    .returning(AlertEvent.alert_id)
                )
                event_id = db.execute(stmt).scalar_one_or_none()
                if event_id is not None:
                    ids.append(event_id)
        db.commit()
        if ids:
            await deliver_pending_alerts(db=db, event_ids=ids)
        return db.query(AlertEvent).filter(AlertEvent.alert_id.in_(ids)).all() if ids else []
    except Exception:
        db.rollback()
        raise
    finally:
        if owned:
            db.close()


async def deliver_pending_alerts(
    ctx: dict | None = None,
    *,
    db: Session | None = None,
    event_ids: list[int] | None = None,
) -> dict[str, int]:

    owned = db is None
    db = db or SessionLocal()
    counts = {"sent": 0, "failed": 0, "suppressed": 0}
    try:
        for _ in range(settings.alert_delivery_batch_size):
            query = db.query(AlertEvent).filter(
                AlertEvent.delivery_status.in_(["pending", "failed"]),
                AlertEvent.delivery_attempts < settings.alert_max_attempts,
                or_(
                    AlertEvent.next_attempt_at.is_(None),
                    AlertEvent.next_attempt_at <= datetime.now(UTC),
                ),
            )
            if event_ids is not None:
                query = query.filter(AlertEvent.alert_id.in_(event_ids))
            event = query.order_by(AlertEvent.alert_id).with_for_update(skip_locked=True).first()
            if event is None:
                break
            listing = db.get(ListingObservation, event.obs_id)
            product = db.get(ProductTemplate, event.product_id)
            rule = db.get(AlertRule, event.rule_id)
            metrics = (
                db.query(ProductDailyMetrics)
                .filter(ProductDailyMetrics.product_id == event.product_id)
                .order_by(ProductDailyMetrics.date.desc())
                .first()
            )
            valuation = _alert_valuation(db, listing) if listing is not None else None
            if (
                not listing
                or not product
                or not rule
                or not rule.is_active
                or not _rule_matches(rule, listing, product, None, metrics, valuation=valuation)
            ):
                event.delivery_status = "suppressed"
                event.suppressed = True
                event.delivery = {
                    "manual_retries": (event.delivery or {}).get("manual_retries", []),
                    "reason": "no_longer_eligible",
                    "valuation": _snapshot(valuation or {}),
                }
                counts["suppressed"] += 1
                db.commit()
                continue
            event.delivery_attempts += 1
            try:
                result = await send_opportunity_alert(
                    {
                        "margin_abs": float(valuation["contribution_eur"]),
                        "margin_pct": (
                            float(listing.price) / float(valuation["estimated_sale_price_eur"]) - 1
                        )
                        * 100,
                        "pmn": float(valuation["estimated_sale_price_eur"]),
                        "max_buy_price_eur": float(valuation["max_buy_price_eur"]),
                        "reference_id": valuation["reference_id"],
                        "destination_marketplace": valuation.get("destination_marketplace"),
                    },
                    {
                        "listing_id": listing.listing_id,
                        "title": listing.title,
                        "price": float(listing.price),
                        "url": listing.url,
                        "condition": listing.condition,
                        "seller_rating": listing.seller_rating,
                    },
                    {
                        "product_id": str(product.product_id),
                        "name": product.name,
                        "brand": product.brand,
                        "description": product.description,
                    },
                    screenshot_path=listing.screenshot_path,
                    alert_id=event.alert_id,
                )
            except Exception as exc:
                logger.warning("Alert {} delivery failed: {}", event.alert_id, type(exc).__name__)
                result = {"status": "error", "error": type(exc).__name__}
            event.delivery = {
                "valuation": _snapshot(valuation),
                "result": result,
                "manual_retries": (event.delivery or {}).get("manual_retries", []),
            }
            if result.get("status") == "success":
                event.delivery_status = "sent"
                event.sent_at = datetime.now(UTC)
                event.next_attempt_at = None
                counts["sent"] += 1
            else:
                event.delivery_status = "failed"
                event.next_attempt_at = (
                    datetime.now(UTC) + timedelta(minutes=2**event.delivery_attempts)
                    if event.delivery_attempts < settings.alert_max_attempts
                    else None
                )
                counts["failed"] += 1
            db.commit()
        return counts
    except Exception:
        db.rollback()
        raise
    finally:
        if owned:
            db.close()

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from libs.common.condition import normalize_condition
from libs.common.models import ListingObservation
from libs.common.settings import settings
from libs.common.valuation_models import ValuationListingReview, VerifiedValuationReference

CENT = Decimal("0.01")
RATE_FIELDS = ("purchase_fee_rate", "sell_fee_rate", "social_charge_rate")
COST_FIELDS = (
    "purchase_fee_fixed_eur",
    "sell_fee_fixed_eur",
    "outbound_logistics_eur",
    "risk_allowance_eur",
    "minimum_contribution_eur",
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _money_floor(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_FLOOR)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return set(re.findall(r"[a-z0-9]+", without_accents))


def _reference_tokens(values: list[str] | None) -> set[str]:
    return set().union(*(_tokens(value) for value in (values or [])))


def _normalized_condition(value: str | None) -> str | None:
    if value in {"new", "like_new", "good", "fair"}:
        return value
    return normalize_condition(value)


def _empty_result(reasons: list[str]) -> dict[str, Any]:
    return {
        "eligible": False,
        "reasons": reasons,
        "reference_id": None,
        "estimated_sale_price_eur": None,
        "acquisition_cost_eur": None,
        "contribution_eur": None,
        "max_buy_price_eur": None,
        "destination_marketplace": None,
        "cost_breakdown": None,
        "reference_snapshot": None,
        "listing_review_snapshot": None,
    }


def _listing_reasons(observation: ListingObservation, now: datetime) -> list[str]:
    reasons: list[str] = []
    delivery = getattr(observation, "delivery_to_france", None)
    if delivery is False:
        reasons.append("france_delivery_unavailable")
    elif (
        delivery is not True
        or not str(getattr(observation, "delivery_evidence", None) or "").strip()
    ):
        reasons.append("france_delivery_unconfirmed")
    if getattr(observation, "product_id", None) is None:
        reasons.append("listing_product_unknown")
    if not getattr(observation, "source", None):
        reasons.append("listing_source_unknown")
    if not str(getattr(observation, "title", "") or "").strip():
        reasons.append("listing_title_missing")
    listing_url = str(getattr(observation, "url", "") or "").strip()
    parsed_url = urlparse(listing_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        reasons.append("listing_url_missing")

    raw_price = getattr(observation, "price", None)
    price = _decimal(raw_price)
    if raw_price is None:
        reasons.append("listing_missing_price")
    elif price is None or price <= 0:
        reasons.append("listing_price_invalid")

    raw_shipping = getattr(observation, "shipping_cost", None)
    shipping = _decimal(raw_shipping)
    if raw_shipping is None:
        reasons.append("listing_missing_shipping_cost")
    elif shipping is None or shipping < 0:
        reasons.append("listing_shipping_cost_invalid")

    if str(getattr(observation, "currency", "") or "").upper() != "EUR":
        reasons.append("listing_currency_not_eur")
    if _normalized_condition(getattr(observation, "condition", None)) is None:
        reasons.append("listing_condition_unknown")
    if getattr(observation, "is_sold", None) is True:
        reasons.append("listing_not_active")
    elif getattr(observation, "is_sold", None) is None:
        reasons.append("listing_status_unknown")
    if getattr(observation, "is_stale", None) is True:
        reasons.append("listing_stale")
    elif getattr(observation, "is_stale", None) is None:
        reasons.append("listing_staleness_unknown")

    last_seen_at = getattr(observation, "last_seen_at", None)
    if last_seen_at is None:
        reasons.append("listing_last_seen_unknown")
    else:
        last_seen_at = _aware(last_seen_at)
        if last_seen_at > now:
            reasons.append("listing_last_seen_in_future")
        elif now - last_seen_at > timedelta(minutes=settings.alert_freshness_minutes):
            reasons.append("listing_last_seen_stale")
    return reasons


def _same_decimal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return _decimal(left) == _decimal(right)


def _review_matches_observation(
    review: ValuationListingReview, observation: ListingObservation
) -> bool:
    return (
        review.is_active is True
        and review.obs_id == observation.obs_id
        and getattr(review, "raw_url", None) == getattr(observation, "url", None)
        and review.raw_title == observation.title
        and getattr(review, "raw_delivery_to_france", None)
        == getattr(observation, "delivery_to_france", None)
        and getattr(review, "raw_delivery_evidence", None)
        == getattr(observation, "delivery_evidence", None)
        and review.raw_condition == observation.condition
        and _same_decimal(review.raw_price_eur, observation.price)
        and _same_decimal(review.raw_shipping_cost_eur, observation.shipping_cost)
    )


def _get_listing_review(
    db: Session, observation: ListingObservation, now: datetime
) -> ValuationListingReview | None:
    reviews = (
        db.query(ValuationListingReview)
        .filter(
            ValuationListingReview.obs_id == observation.obs_id,
            ValuationListingReview.is_active.is_(True),
            ValuationListingReview.reviewed_at <= now,
            ValuationListingReview.expires_at > now,
        )
        .order_by(
            ValuationListingReview.reviewed_at.desc(),
            ValuationListingReview.review_id.desc(),
        )
        .all()
    )
    valid_reviews = [
        review
        for review in reviews
        if _review_matches_observation(review, observation)
        and _aware(review.reviewed_at) <= now < _aware(review.expires_at)
    ]
    return max(
        valid_reviews,
        key=lambda review: (_aware(review.reviewed_at), str(review.review_id)),
        default=None,
    )


def _effective_observation(
    observation: ListingObservation, review: ValuationListingReview | None
) -> SimpleNamespace:
    fields = (
        "obs_id",
        "product_id",
        "source",
        "title",
        "url",
        "price",
        "shipping_cost",
        "delivery_to_france",
        "delivery_evidence",
        "currency",
        "condition",
        "is_sold",
        "is_stale",
        "last_seen_at",
    )
    values = {field: getattr(observation, field, None) for field in fields}
    if review is not None:
        values.update(
            {
                "title": review.reviewed_title,
                "condition": review.reviewed_condition,
                "shipping_cost": review.reviewed_shipping_cost_eur,
            }
        )
    if review is not None and getattr(review, "reviewed_delivery_to_france", None) is not None:
        values["delivery_to_france"] = review.reviewed_delivery_to_france
        values["delivery_evidence"] = review.delivery_evidence
    return SimpleNamespace(**values)


def _listing_review_snapshot(review: ValuationListingReview) -> dict[str, Any]:
    return {
        "review_id": str(review.review_id),
        "reviewed_delivery_to_france": getattr(review, "reviewed_delivery_to_france", None),
        "delivery_evidence": getattr(review, "delivery_evidence", None),
        "reviewed_title": review.reviewed_title,
        "reviewed_condition": review.reviewed_condition,
        "reviewed_shipping_cost_eur": review.reviewed_shipping_cost_eur,
        "reviewed_by": review.reviewed_by,
        "reviewed_at": review.reviewed_at,
        "expires_at": review.expires_at,
        "notes": review.notes,
        "raw_title": review.raw_title,
        "raw_price_eur": review.raw_price_eur,
        "raw_condition": review.raw_condition,
        "raw_shipping_cost_eur": review.raw_shipping_cost_eur,
    }


def _reference_reasons(
    reference: VerifiedValuationReference,
    observation: ListingObservation,
    now: datetime,
) -> list[str]:
    reasons: list[str] = []
    from ingestion.relevance import classify_listing_relevance

    target = " ".join(reference.required_tokens or [])
    relevance = classify_listing_relevance(target, target, str(observation.title or ""))
    if not relevance.is_relevant:
        reasons.append(f"relevance_{relevance.classification.value}")
    title_tokens = _tokens(str(observation.title or ""))
    required = _reference_tokens(reference.required_tokens)
    excluded = _reference_tokens(reference.excluded_tokens)
    if not required or not required.issubset(title_tokens):
        reasons.append("required_tokens_missing")
    if excluded.intersection(title_tokens):
        reasons.append("excluded_tokens_present")
    if _normalized_condition(observation.condition) != reference.condition:
        reasons.append("condition_mismatch")
    if str(reference.currency or "").upper() != "EUR":
        reasons.append("reference_currency_not_eur")

    evidence_values = (
        reference.evidence_url,
        reference.comparable_sold_at,
        reference.reviewed_at,
        reference.reviewed_by,
        reference.expires_at,
    )
    if any(
        value is None or (isinstance(value, str) and not value.strip()) for value in evidence_values
    ):
        reasons.append("reference_evidence_incomplete")
    else:
        evidence_url = urlparse(str(reference.evidence_url))
        if evidence_url.scheme not in {"http", "https"} or not evidence_url.netloc:
            reasons.append("reference_evidence_invalid")
        sold_at = _aware(reference.comparable_sold_at)
        reviewed_at = _aware(reference.reviewed_at)
        expires_at = _aware(reference.expires_at)
        if sold_at > reviewed_at or reviewed_at >= expires_at:
            reasons.append("reference_evidence_window_invalid")
        if sold_at > now or reviewed_at > now:
            reasons.append("reference_evidence_in_future")
        if now >= expires_at:
            reasons.append("reference_expired")

    raw_rates = [getattr(reference, field, None) for field in RATE_FIELDS]
    raw_costs = [getattr(reference, field, None) for field in COST_FIELDS]
    raw_sale_price = reference.reviewed_price_eur
    rate_values = [_decimal(value) for value in raw_rates]
    cost_values = [_decimal(value) for value in raw_costs]
    sale_price = _decimal(raw_sale_price)
    if raw_sale_price is None or any(value is None for value in raw_rates + raw_costs):
        reasons.append("reference_costs_incomplete")
    elif (
        sale_price is None
        or sale_price <= 0
        or any(value is None or value < 0 or value > 1 for value in rate_values)
        or any(value is None or value < 0 for value in cost_values)
    ):
        reasons.append("reference_costs_invalid")
    return reasons


def _reference_snapshot(reference: VerifiedValuationReference) -> dict[str, Any]:
    return {
        "purchase_source": reference.purchase_source,
        "destination_marketplace": reference.destination_marketplace,
        "required_tokens": list(reference.required_tokens or []),
        "excluded_tokens": list(reference.excluded_tokens or []),
        "condition": reference.condition,
        "currency": reference.currency,
        "evidence_url": reference.evidence_url,
        "comparable_sold_at": reference.comparable_sold_at,
        "reviewed_at": reference.reviewed_at,
        "reviewed_by": reference.reviewed_by,
        "expires_at": reference.expires_at,
        "limitations": reference.limitations,
    }


def _evaluate_reference(
    reference: VerifiedValuationReference,
    observation: ListingObservation,
    now: datetime,
    listing_reasons: list[str],
) -> dict[str, Any]:
    reasons = list(listing_reasons)
    reasons.extend(_reference_reasons(reference, observation, now))

    sale_price = _decimal(reference.reviewed_price_eur)
    listing_price = _decimal(observation.price)
    shipping = _decimal(observation.shipping_cost)
    purchase_rate = _decimal(reference.purchase_fee_rate)
    purchase_fixed = _decimal(reference.purchase_fee_fixed_eur)
    sell_rate = _decimal(reference.sell_fee_rate)
    sell_fixed = _decimal(reference.sell_fee_fixed_eur)
    social_rate = _decimal(reference.social_charge_rate)
    outbound = _decimal(reference.outbound_logistics_eur)
    risk = _decimal(reference.risk_allowance_eur)
    minimum = _decimal(reference.minimum_contribution_eur)
    financial_values = (
        sale_price,
        listing_price,
        shipping,
        purchase_rate,
        purchase_fixed,
        sell_rate,
        sell_fixed,
        social_rate,
        outbound,
        risk,
        minimum,
    )
    result = _empty_result(reasons)
    result["reference_id"] = str(reference.reference_id)
    result["destination_marketplace"] = reference.destination_marketplace
    result["reference_snapshot"] = _reference_snapshot(reference)
    if any(value is None for value in financial_values):
        return result

    sale_price = cast(Decimal, sale_price)
    listing_price = cast(Decimal, listing_price)
    shipping = cast(Decimal, shipping)
    purchase_rate = cast(Decimal, purchase_rate)
    purchase_fixed = cast(Decimal, purchase_fixed)
    sell_rate = cast(Decimal, sell_rate)
    sell_fixed = cast(Decimal, sell_fixed)
    social_rate = cast(Decimal, social_rate)
    outbound = cast(Decimal, outbound)
    risk = cast(Decimal, risk)
    minimum = cast(Decimal, minimum)

    if (
        sale_price <= 0
        or listing_price <= 0
        or shipping < 0
        or purchase_rate < 0
        or purchase_rate > 1
        or sell_rate < 0
        or sell_rate > 1
        or social_rate < 0
        or social_rate > 1
        or any(value < 0 for value in (purchase_fixed, sell_fixed, outbound, risk, minimum))
    ):
        return result

    purchase_fee = _money(purchase_fixed + listing_price * purchase_rate)
    acquisition = _money(listing_price + shipping + purchase_fee)
    sell_fee = _money(sell_fixed + sale_price * sell_rate)
    social_charge = _money(sale_price * social_rate)
    contribution = _money(sale_price - acquisition - sell_fee - social_charge - outbound - risk)
    max_buy = _money_floor(
        (
            sale_price
            - sell_fee
            - social_charge
            - outbound
            - risk
            - shipping
            - purchase_fixed
            - minimum
        )
        / (Decimal("1") + purchase_rate)
    )
    if contribution < minimum:
        reasons.append("below_minimum_contribution")

    result.update(
        {
            "eligible": not reasons,
            "reasons": list(dict.fromkeys(reasons)),
            "estimated_sale_price_eur": _money(sale_price),
            "acquisition_cost_eur": acquisition,
            "contribution_eur": contribution,
            "max_buy_price_eur": max_buy,
            "cost_breakdown": {
                "listing_price_eur": _money(listing_price),
                "inbound_shipping_eur": _money(shipping),
                "purchase_fee_eur": purchase_fee,
                "sell_fee_eur": sell_fee,
                "social_charge_eur": social_charge,
                "outbound_logistics_eur": _money(outbound),
                "risk_allowance_eur": _money(risk),
                "minimum_contribution_eur": _money(minimum),
            },
        }
    )
    return result


def evaluate_valuation(
    db: Session, observation: ListingObservation, now: datetime | None = None
) -> dict[str, Any]:
    evaluation_time = _aware(now or datetime.now(UTC))
    review = _get_listing_review(db, observation, evaluation_time)
    effective_observation = _effective_observation(observation, review)
    listing_reasons = _listing_reasons(effective_observation, evaluation_time)
    if getattr(observation, "product_id", None) is None or not getattr(observation, "source", None):
        result = _empty_result(listing_reasons)
        if review is not None:
            result["listing_review_snapshot"] = _listing_review_snapshot(review)
        return result

    references = (
        db.query(VerifiedValuationReference)
        .filter(
            VerifiedValuationReference.product_id == observation.product_id,
            VerifiedValuationReference.purchase_source == observation.source,
            VerifiedValuationReference.is_active.is_(True),
        )
        .order_by(
            VerifiedValuationReference.reviewed_at.desc(),
            VerifiedValuationReference.reference_id.desc(),
        )
        .all()
    )
    references = [
        reference
        for reference in references
        if str(reference.product_id) == str(observation.product_id)
        and reference.purchase_source == observation.source
        and reference.is_active is True
    ]
    if not references:
        result = _empty_result([*listing_reasons, "no_verified_reference"])
        if review is not None:
            result["listing_review_snapshot"] = _listing_review_snapshot(review)
        return result

    def recency_key(reference: VerifiedValuationReference) -> tuple[datetime, str]:
        reviewed_at = reference.reviewed_at or datetime.min.replace(tzinfo=UTC)
        return (_aware(reviewed_at), str(reference.reference_id))

    references.sort(key=recency_key, reverse=True)
    evaluations = [
        _evaluate_reference(reference, effective_observation, evaluation_time, listing_reasons)
        for reference in references
    ]
    if review is not None:
        review_snapshot = _listing_review_snapshot(review)
        for result in evaluations:
            result["listing_review_snapshot"] = review_snapshot
    eligible = [result for result in evaluations if result["eligible"]]
    if not eligible:
        return evaluations[0]

    reference_by_id = {str(reference.reference_id): reference for reference in references}
    return max(
        eligible,
        key=lambda result: (
            result["contribution_eur"],
            recency_key(reference_by_id[result["reference_id"]]),
        ),
    )

"""Turn eBay.fr completed sales into valuation proposals awaiting operator approval.

Proposals are evidence only. ``ingestion.valuation.evaluate_valuation`` reads
``verified_valuation_reference`` exclusively, so nothing here can authorize an alert.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Any, cast

from loguru import logger
from sqlalchemy.orm import Session

from ingestion.connectors.ebay_sold import (
    COOLDOWN_SOURCE,
    SoldSale,
    build_sold_search_url,
    fetch_ebay_sold_page,
)
from ingestion.filtering import _matches_words_to_avoid
from ingestion.valuation import _reference_reasons, _reference_tokens, _tokens
from libs.common.condition import normalize_condition
from libs.common.cooldown import source_cooldown_active
from libs.common.db import SessionLocal
from libs.common.models import ListingObservation, ProductTemplate
from libs.common.settings import settings
from libs.common.sold_comp_models import SoldCompProposal

CENT = Decimal("0.01")
REFRESH_KEY_PREFIX = "sold_comps:last_fetch:"
SUPERSEDABLE_STATUSES = ("pending", "insufficient")


@dataclass(frozen=True)
class IdentitySpec:
    search_query: str
    required_tokens: list[str]
    excluded_tokens: list[str]
    words_to_avoid: list[str]


@dataclass(frozen=True)
class ProposalDraft:
    condition: str
    status: str
    sales: list[SoldSale]
    median_price_eur: Decimal | None
    p25_price_eur: Decimal | None
    p75_price_eur: Decimal | None
    first_sold_at: datetime | None
    last_sold_at: datetime | None


def identity_spec_for_product(product: ProductTemplate) -> IdentitySpec:
    """Required tokens come from the exact search query; single-word avoid terms exclude."""
    search_query = str(product.search_query or "").strip()
    required = search_query.split()
    required_tokens = _reference_tokens(required)
    excluded: list[str] = []
    phrases: list[str] = []
    for word in product.words_to_avoid or []:
        tokens = _tokens(str(word))
        if len(tokens) == 1 and not tokens & required_tokens:
            excluded.append(str(word))
        elif len(tokens) > 1:
            phrases.append(str(word))
    return IdentitySpec(search_query, required, excluded, phrases)


def identity_rejection(title: str, spec: IdentitySpec) -> str | None:
    """Apply the valuation gate's own identity checks to a sold title."""
    reference = SimpleNamespace(
        required_tokens=spec.required_tokens,
        excluded_tokens=spec.excluded_tokens,
        condition=None,
        currency="EUR",
        evidence_url=None,
        comparable_sold_at=None,
        reviewed_at=None,
        reviewed_by=None,
        expires_at=None,
        reviewed_price_eur=None,
    )
    observation = SimpleNamespace(title=title, condition=None)
    reasons = _reference_reasons(cast(Any, reference), cast(Any, observation), datetime.now(UTC))
    for reason in reasons:
        if reason.startswith("relevance_") or reason in {
            "required_tokens_missing",
            "excluded_tokens_present",
        }:
            return reason
    if not _matches_words_to_avoid(
        cast(Any, SimpleNamespace(words_to_avoid=spec.words_to_avoid)),
        cast(Any, SimpleNamespace(title=title)),
    ):
        return "words_to_avoid_present"
    return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _quartiles(prices: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    ordered = sorted(prices)
    if len(ordered) == 1:
        return ordered[0], ordered[0], ordered[0]
    p25, median, p75 = statistics.quantiles(ordered, n=4, method="inclusive")
    return _money(p25), _money(median), _money(p75)


def _excluded(sale: SoldSale, reason: str) -> dict[str, Any]:
    return {**sale.as_dict(), "reason": reason}


def build_proposal_drafts(
    sales: list[SoldSale],
    spec: IdentitySpec,
    *,
    decision_at: datetime,
    window_days: int,
    min_comparables: int,
    excluded_item_ids: set[str],
) -> tuple[list[ProposalDraft], list[dict[str, Any]]]:
    """Group identity-matching sales by normalized condition and summarize item prices.

    Only sales dated before ``decision_at`` and inside the window count; sales whose item
    id is a listing the system observed for this product are excluded to avoid leakage.
    """
    window_start = decision_at - timedelta(days=window_days)
    groups: dict[str, list[SoldSale]] = defaultdict(list)
    excluded: list[dict[str, Any]] = []
    for sale in sales:
        condition = normalize_condition(sale.condition_raw)
        if sale.item_id in excluded_item_ids:
            excluded.append(_excluded(sale, "observed_listing_overlap"))
        elif sale.sold_at is None:
            excluded.append(_excluded(sale, "sold_at_unknown"))
        elif sale.sold_at > decision_at:
            excluded.append(_excluded(sale, "sold_after_decision"))
        elif sale.sold_at < window_start:
            excluded.append(_excluded(sale, "outside_window"))
        elif condition is None:
            excluded.append(_excluded(sale, "condition_unknown"))
        elif (reason := identity_rejection(sale.title, spec)) is not None:
            excluded.append(_excluded(sale, reason))
        else:
            groups[condition].append(sale)

    drafts: list[ProposalDraft] = []
    for condition in sorted(groups):
        group = groups[condition]
        p25, median, p75 = _quartiles([sale.price_eur for sale in group])
        sold_dates = [cast(datetime, sale.sold_at) for sale in group]
        drafts.append(
            ProposalDraft(
                condition=condition,
                status="pending" if len(group) >= min_comparables else "insufficient",
                sales=sorted(group, key=lambda sale: cast(datetime, sale.sold_at), reverse=True),
                median_price_eur=median,
                p25_price_eur=p25,
                p75_price_eur=p75,
                first_sold_at=min(sold_dates),
                last_sold_at=max(sold_dates),
            )
        )
    return drafts, excluded


def observed_ebay_item_ids(db: Session, product_id: Any) -> set[str]:
    rows = (
        db.query(ListingObservation.listing_id)
        .filter(
            ListingObservation.product_id == product_id,
            ListingObservation.source == "ebay",
        )
        .all()
    )
    item_ids: set[str] = set()
    for (listing_id,) in rows:
        value = str(listing_id or "")
        parts = value.split("|")
        candidate = parts[1] if len(parts) == 3 else value
        if candidate.isdigit():
            item_ids.add(candidate)
    return item_ids


def persist_proposals(
    db: Session,
    product: ProductTemplate,
    spec: IdentitySpec,
    drafts: list[ProposalDraft],
    excluded: list[dict[str, Any]],
    *,
    decision_at: datetime,
    min_comparables: int,
) -> list[SoldCompProposal]:
    created: list[SoldCompProposal] = []
    for draft in drafts:
        superseded = (
            db.query(SoldCompProposal)
            .filter(
                SoldCompProposal.product_id == product.product_id,
                SoldCompProposal.condition == draft.condition,
                SoldCompProposal.status.in_(SUPERSEDABLE_STATUSES),
            )
            .with_for_update()
            .all()
        )
        for previous in superseded:
            previous.status = "rejected"
            previous.reviewed_by = "system"
            previous.reviewed_at = decision_at
            previous.review_note = "superseded by a newer sold-comps refresh"
        proposal = SoldCompProposal(
            product_id=product.product_id,
            condition=draft.condition,
            status=draft.status,
            search_query=spec.search_query,
            search_url=build_sold_search_url(spec.search_query),
            required_tokens=spec.required_tokens,
            excluded_tokens=spec.excluded_tokens,
            sale_count=len(draft.sales),
            min_comparables=min_comparables,
            median_price_eur=draft.median_price_eur,
            p25_price_eur=draft.p25_price_eur,
            p75_price_eur=draft.p75_price_eur,
            first_sold_at=draft.first_sold_at,
            last_sold_at=draft.last_sold_at,
            computed_at=decision_at,
            sales=[sale.as_dict() for sale in draft.sales],
            excluded_sales=excluded,
        )
        db.add(proposal)
        created.append(proposal)
    db.commit()
    return created


def _recently_refreshed(db: Session, product_id: Any, now: datetime) -> bool:
    cutoff = now - timedelta(days=settings.sold_comps_refresh_days)
    return (
        db.query(SoldCompProposal.proposal_id)
        .filter(
            SoldCompProposal.product_id == product_id,
            SoldCompProposal.computed_at >= cutoff,
        )
        .first()
        is not None
    )


async def refresh_sold_comps(ctx: dict) -> dict[str, Any]:
    """Refresh at most one sold-comps page per due active product, bounded per run."""
    if not settings.sold_comps_enabled:
        return {"status": "disabled"}
    redis = ctx.get("redis") or ctx.get("pool")
    if redis is None:
        return {"status": "error", "reason": "redis_unavailable"}
    if await source_cooldown_active(redis, COOLDOWN_SOURCE):
        return {"status": "cooldown"}

    now = datetime.now(UTC)
    with SessionLocal() as db:
        products = [
            (str(product.product_id), str(product.search_query or ""))
            for product in db.query(ProductTemplate)
            .filter(ProductTemplate.is_active.is_(True))
            .order_by(ProductTemplate.product_id)
            .all()
            if not _recently_refreshed(db, product.product_id, now)
        ]

    requests = 0
    outcomes: dict[str, Any] = {}
    for product_id, search_query in products:
        if requests >= settings.sold_comps_max_requests_per_run:
            break
        if not search_query.strip():
            outcomes[product_id] = "search_query_missing"
            continue
        refresh_key = f"{REFRESH_KEY_PREFIX}{product_id}"
        if await redis.exists(refresh_key):
            continue
        await redis.set(refresh_key, now.isoformat(), ex=settings.sold_comps_refresh_days * 86400)
        requests += 1
        result = await fetch_ebay_sold_page(search_query, redis)
        if result.status != "ok" or result.page is None:
            outcomes[product_id] = result.status
            if result.status in {"blocked", "cooldown"}:
                break
            continue
        decision_at = datetime.now(UTC)
        with SessionLocal() as db:
            product = db.get(ProductTemplate, product_id)
            if product is None:
                continue
            spec = identity_spec_for_product(product)
            drafts, excluded = build_proposal_drafts(
                result.page.sales,
                spec,
                decision_at=decision_at,
                window_days=settings.sold_comps_window_days,
                min_comparables=settings.sold_comps_min_comparables,
                excluded_item_ids=observed_ebay_item_ids(db, product.product_id),
            )
            excluded.extend(card.as_dict() for card in result.page.rejected)
            created = persist_proposals(
                db,
                product,
                spec,
                drafts,
                excluded,
                decision_at=decision_at,
                min_comparables=settings.sold_comps_min_comparables,
            )
            outcomes[product_id] = {
                "status": "ok",
                "proposals": {proposal.condition: proposal.status for proposal in created},
                "excluded": len(excluded),
            }
    logger.info("Sold-comps refresh: {} request(s), outcomes={}", requests, outcomes)
    return {"status": "ok", "requests": requests, "products": outcomes}

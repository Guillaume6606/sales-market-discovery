from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from ingestion import sold_comps
from ingestion.connectors.ebay_sold import SoldSale, parse_sold_search_html
from ingestion.sold_comps import (
    build_proposal_drafts,
    identity_rejection,
    identity_spec_for_product,
)
from ingestion.valuation import evaluate_valuation
from libs.common.sold_comp_models import SoldCompProposal

FIXTURE = Path(__file__).parent / "fixtures" / "ebay_sold_sony_wh1000xm4.synthetic.html"
DECISION_AT = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
PRODUCT_ID = UUID("00000000-0000-0000-0000-000000000001")


def _product(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "product_id": PRODUCT_ID,
        "search_query": "Sony WH-1000XM4",
        "words_to_avoid": ["coussinets", "pour pièces", "sony"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _sale(item_id: str, price: str, days_ago: int, **overrides: Any) -> SoldSale:
    values: dict[str, Any] = {
        "item_id": item_id,
        "title": "Sony WH-1000XM4 casque noir",
        "price_eur": Decimal(price),
        "currency": "EUR",
        "shipping_eur": Decimal("5.00"),
        "sold_at": DECISION_AT - timedelta(days=days_ago),
        "condition_raw": "Occasion",
        "url": f"https://www.ebay.fr/itm/{item_id}",
    }
    values.update(overrides)
    return SoldSale(**values)


def _drafts(
    sales: list[SoldSale], min_comparables: int = 8, excluded_ids: set[str] | None = None
) -> tuple[list[Any], list[dict[str, Any]]]:
    return build_proposal_drafts(
        sales,
        identity_spec_for_product(_product()),
        decision_at=DECISION_AT,
        window_days=90,
        min_comparables=min_comparables,
        excluded_item_ids=excluded_ids or set(),
    )


def test_identity_spec_keeps_query_and_single_word_exclusions() -> None:
    spec = identity_spec_for_product(_product())

    assert spec.required_tokens == ["Sony", "WH-1000XM4"]
    assert spec.excluded_tokens == ["coussinets"]
    assert spec.words_to_avoid == ["pour pièces"]


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Sony WH-1000XM4 casque noir", None),
        ("Sony WH-1000XM5 casque noir", "relevance_wrong_variant"),
        ("Coussinets pour Sony WH-1000XM4", "excluded_tokens_present"),
        ("Sony WH-1000XM4 HS ne s'allume plus", "relevance_parts_broken"),
    ],
)
def test_identity_rejection_reuses_valuation_gate(title: str, reason: str | None) -> None:
    assert identity_rejection(title, identity_spec_for_product(_product())) == reason


def test_fixture_groups_by_condition_and_reports_exclusions() -> None:
    page = parse_sold_search_html(FIXTURE.read_text(encoding="utf-8"))

    drafts, excluded = _drafts(page.sales)

    by_condition = {draft.condition: draft for draft in drafts}
    good = by_condition["good"]
    assert good.status == "insufficient"
    assert [sale.item_id for sale in good.sales] == [
        "356001000001",
        "356001000002",
        "356001000006",
        "356001000013",
        "356001000014",
        "356001000015",
    ]
    reasons = {row["item_id"]: row["reason"] for row in excluded}
    assert reasons == {
        "356001000007": "condition_unknown",
        "356001000008": "relevance_wrong_variant",
        "356001000009": "excluded_tokens_present",
        "356001000012": "sold_at_unknown",
        "356001000016": "outside_window",
    }
    assert [sale.item_id for sale in by_condition["new"].sales] == ["356001000010"]
    assert len(by_condition["like_new"].sales) == 1


def test_aggregation_median_quartiles_and_window() -> None:
    page = parse_sold_search_html(FIXTURE.read_text(encoding="utf-8"))

    drafts, _excluded = _drafts(page.sales, min_comparables=5)

    good = next(draft for draft in drafts if draft.condition == "good")
    assert good.status == "pending"
    assert good.median_price_eur == Decimal("142.50")
    assert good.p25_price_eur == Decimal("137.75")
    assert good.p75_price_eur == Decimal("147.25")
    assert good.first_sold_at == datetime(2026, 9, 5, tzinfo=UTC)
    assert good.last_sold_at == datetime(2026, 11, 3, tzinfo=UTC)


def test_minimum_comparables_threshold_is_inclusive() -> None:
    sales = [_sale(f"10000000000{index}", str(100 + index), index + 1) for index in range(8)]

    at_threshold, _ = _drafts(sales, min_comparables=8)
    below_threshold, _ = _drafts(sales[:7], min_comparables=8)

    assert at_threshold[0].status == "pending"
    assert below_threshold[0].status == "insufficient"


def test_comparables_exclude_observed_listing_and_future_sales() -> None:
    sales = [
        _sale("200000000001", "100", 3),
        _sale("200000000002", "999", 1),
        _sale("200000000003", "120", -1),
    ]

    drafts, excluded = _drafts(sales, min_comparables=1, excluded_ids={"200000000002"})

    assert [sale.item_id for sale in drafts[0].sales] == ["200000000001"]
    assert {row["item_id"]: row["reason"] for row in excluded} == {
        "200000000002": "observed_listing_overlap",
        "200000000003": "sold_after_decision",
    }
    assert drafts[0].last_sold_at is not None and drafts[0].last_sold_at <= DECISION_AT


def test_observed_ebay_item_ids_extracts_legacy_ids() -> None:
    class _Query:
        def filter(self, *_args: object) -> "_Query":
            return self

        def all(self) -> list[tuple[str | None]]:
            return [("v1|356001000001|0",), ("356001000002",), (None,), ("abc",)]

    db = SimpleNamespace(query=lambda *_args: _Query())

    assert sold_comps.observed_ebay_item_ids(db, PRODUCT_ID) == {"356001000001", "356001000002"}


class _GateQuery:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def filter(self, *_args: object) -> "_GateQuery":
        return self

    def order_by(self, *_args: object) -> "_GateQuery":
        return self

    def all(self) -> list[Any]:
        return self.rows


class _GateSession:
    def __init__(self, proposals: list[SoldCompProposal]) -> None:
        self.proposals = proposals

    def query(self, model: object) -> _GateQuery:
        if model is SoldCompProposal:
            return _GateQuery(self.proposals)
        return _GateQuery([])


def test_unapproved_proposal_never_authorizes_an_alert() -> None:
    proposal = SoldCompProposal(
        product_id=PRODUCT_ID,
        condition="good",
        status="pending",
        median_price_eur=Decimal("500.00"),
        sale_count=20,
        min_comparables=8,
    )
    observation = SimpleNamespace(
        obs_id=1,
        product_id=PRODUCT_ID,
        source="leboncoin",
        title="Sony WH-1000XM4 casque noir",
        url="https://example.com/listing/1",
        price=Decimal("50.00"),
        shipping_cost=Decimal("0.00"),
        delivery_to_france=True,
        delivery_evidence="checkout confirmed",
        currency="EUR",
        condition="Bon état",
        is_sold=False,
        is_stale=False,
        last_seen_at=DECISION_AT - timedelta(minutes=1),
    )

    result = evaluate_valuation(_GateSession([proposal]), observation, now=DECISION_AT)

    assert result["eligible"] is False
    assert "no_verified_reference" in result["reasons"]


class _FakeRedis:
    def __init__(self, cooldown: bool = False) -> None:
        self.keys: dict[str, str] = {}
        self.cooldown = cooldown

    async def exists(self, key: str) -> int:
        if key.startswith("ingestion:cooldown:"):
            return int(self.cooldown)
        return int(key in self.keys)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.keys[key] = value


async def test_refresh_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sold_comps.settings, "sold_comps_enabled", False)
    fetch = AsyncMock()
    monkeypatch.setattr(sold_comps, "fetch_ebay_sold_page", fetch)

    assert await sold_comps.refresh_sold_comps({"redis": _FakeRedis()}) == {"status": "disabled"}
    fetch.assert_not_awaited()


class _ProductQuery:
    def __init__(self, products: list[SimpleNamespace]) -> None:
        self.products = products

    def filter(self, *_args: object) -> "_ProductQuery":
        return self

    def order_by(self, *_args: object) -> "_ProductQuery":
        return self

    def all(self) -> list[SimpleNamespace]:
        return self.products


class _JobSession:
    def __init__(self, products: list[SimpleNamespace]) -> None:
        self.products = products

    def __enter__(self) -> "_JobSession":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def query(self, *_args: object) -> _ProductQuery:
        return _ProductQuery(self.products)


async def test_refresh_bounds_requests_and_respects_weekly_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    products = [
        SimpleNamespace(product_id=f"p{index}", search_query=f"Query {index}") for index in range(5)
    ]
    redis = _FakeRedis()
    redis.keys[f"{sold_comps.REFRESH_KEY_PREFIX}p0"] = "recent"
    monkeypatch.setattr(sold_comps.settings, "sold_comps_enabled", True)
    monkeypatch.setattr(sold_comps.settings, "sold_comps_max_requests_per_run", 2)
    monkeypatch.setattr(sold_comps, "SessionLocal", lambda: _JobSession(products))
    monkeypatch.setattr(sold_comps, "_recently_refreshed", lambda *_args: False)
    fetch = AsyncMock(return_value=SimpleNamespace(status="unrecognized_page", page=None))
    monkeypatch.setattr(sold_comps, "fetch_ebay_sold_page", fetch)

    result = await sold_comps.refresh_sold_comps({"redis": redis})

    assert result["requests"] == 2
    assert [call.args[0] for call in fetch.await_args_list] == ["Query 1", "Query 2"]
    assert f"{sold_comps.REFRESH_KEY_PREFIX}p1" in redis.keys


async def test_refresh_stops_after_block(monkeypatch: pytest.MonkeyPatch) -> None:
    products = [SimpleNamespace(product_id=f"p{index}", search_query="Q") for index in range(3)]
    monkeypatch.setattr(sold_comps.settings, "sold_comps_enabled", True)
    monkeypatch.setattr(sold_comps.settings, "sold_comps_max_requests_per_run", 3)
    monkeypatch.setattr(sold_comps, "SessionLocal", lambda: _JobSession(products))
    monkeypatch.setattr(sold_comps, "_recently_refreshed", lambda *_args: False)
    fetch = AsyncMock(return_value=SimpleNamespace(status="blocked", page=None))
    monkeypatch.setattr(sold_comps, "fetch_ebay_sold_page", fetch)

    result = await sold_comps.refresh_sold_comps({"redis": _FakeRedis()})

    assert result["requests"] == 1
    assert result["products"] == {"p0": "blocked"}


async def test_refresh_skips_during_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sold_comps.settings, "sold_comps_enabled", True)
    fetch = AsyncMock()
    monkeypatch.setattr(sold_comps, "fetch_ebay_sold_page", fetch)

    result = await sold_comps.refresh_sold_comps({"redis": _FakeRedis(cooldown=True)})

    assert result == {"status": "cooldown"}
    fetch.assert_not_awaited()

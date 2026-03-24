"""Tests for detail fetch candidate selection logic."""

from decimal import Decimal

from ingestion.detail_fetch import should_fetch_detail


def test_fetch_when_price_below_pmn_threshold():
    assert (
        should_fetch_detail(
            price=Decimal("80"),
            pmn=Decimal("100"),
            pmn_threshold=1.1,
            price_min=None,
            price_max=None,
        )
        is True
    )


def test_skip_when_price_above_pmn_threshold():
    assert (
        should_fetch_detail(
            price=Decimal("120"),
            pmn=Decimal("100"),
            pmn_threshold=1.1,
            price_min=None,
            price_max=None,
        )
        is False
    )


def test_fetch_when_no_pmn_but_price_range():
    assert (
        should_fetch_detail(
            price=Decimal("80"),
            pmn=None,
            pmn_threshold=1.1,
            price_min=Decimal("50"),
            price_max=Decimal("200"),
        )
        is True
    )


def test_fetch_all_when_cold_start():
    assert (
        should_fetch_detail(
            price=Decimal("999"),
            pmn=None,
            pmn_threshold=1.1,
            price_min=None,
            price_max=None,
        )
        is True
    )


def test_fetch_at_pmn_boundary():
    assert (
        should_fetch_detail(
            price=Decimal("110"),
            pmn=Decimal("100"),
            pmn_threshold=1.1,
            price_min=None,
            price_max=None,
        )
        is True
    )


def test_none_price_returns_false():
    assert (
        should_fetch_detail(
            price=None,
            pmn=Decimal("100"),
            pmn_threshold=1.1,
            price_min=None,
            price_max=None,
        )
        is False
    )

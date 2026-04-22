"""Centralized API client for the Market Discovery backend."""

from typing import Any

import httpx
import streamlit as st

from ui.lib.config import DEFAULT_TIMEOUT, get_api_url

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _url(path: str) -> str:
    return f"{get_api_url()}{path}"


def api_get(path: str, **kwargs: Any) -> httpx.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return httpx.get(_url(path), **kwargs)


def api_post(path: str, **kwargs: Any) -> httpx.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return httpx.post(_url(path), **kwargs)


def api_put(path: str, **kwargs: Any) -> httpx.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return httpx.put(_url(path), **kwargs)


def api_delete(path: str, **kwargs: Any) -> httpx.Response:
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return httpx.delete(_url(path), **kwargs)


# ---------------------------------------------------------------------------
# Discovery & Products
# ---------------------------------------------------------------------------


@st.cache_data(ttl=10)
def fetch_discovery(
    category: str = "",
    brand: str = "",
    min_margin: float | None = None,
    max_margin: float | None = None,
    min_liquidity: float | None = None,
    min_trend: float | None = None,
    min_pmn_confidence: float | None = None,
    sort_by: str = "margin",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        params: dict[str, Any] = {"sort_by": sort_by, "limit": limit, "offset": offset}
        if category:
            params["category"] = category
        if brand:
            params["brand"] = brand
        if min_margin is not None:
            params["min_margin"] = min_margin
        if max_margin is not None:
            params["max_margin"] = max_margin
        if min_liquidity is not None:
            params["min_liquidity"] = min_liquidity
        if min_trend is not None:
            params["min_trend"] = min_trend
        if min_pmn_confidence is not None:
            params["min_pmn_confidence"] = min_pmn_confidence
        r = api_get("/products/discovery", params=params)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Failed to load opportunities: {exc}")
        return {"items": [], "total": 0, "offset": 0, "limit": limit}


@st.cache_data(ttl=10)
def fetch_product_detail(product_id: str) -> dict[str, Any] | None:
    try:
        r = api_get(f"/products/{product_id}", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Failed to load product details: {exc}")
        return None


@st.cache_data(ttl=10)
def fetch_price_history(product_id: str, days: int = 30) -> dict[str, Any] | None:
    try:
        r = api_get(f"/products/{product_id}/price-history", params={"days": days}, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=10)
def fetch_pmn_accuracy(product_id: str) -> dict[str, Any] | None:
    try:
        r = api_get(f"/products/{product_id}/pmn-accuracy", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=10)
def fetch_filtering_stats(product_id: str) -> dict[str, Any] | None:
    try:
        r = api_get(f"/products/{product_id}/filtering-stats", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=30)
def fetch_analytics() -> dict[str, Any] | None:
    try:
        r = api_get("/analytics/overview", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=30)
def fetch_categories() -> list[dict[str, Any]]:
    """Return raw category dicts (category_id, name, description)."""
    try:
        r = api_get("/categories", timeout=10.0)
        r.raise_for_status()
        return r.json().get("categories", [])
    except Exception:
        return []


def fetch_category_names() -> list[str]:
    return [cat["name"] for cat in fetch_categories()]


@st.cache_data(ttl=10)
def fetch_products(active: bool | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if active is not None:
        params["is_active"] = active
    try:
        r = api_get("/products", params=params, timeout=10.0)
        r.raise_for_status()
        return r.json().get("products", [])
    except Exception as exc:
        st.error(f"Failed to load products: {exc}")
        return []


# ---------------------------------------------------------------------------
# Health & Observability
# ---------------------------------------------------------------------------


@st.cache_data(ttl=15)
def fetch_health_overview() -> dict[str, Any] | None:
    try:
        r = api_get("/health/overview", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=15)
def fetch_ingestion_health() -> list[dict[str, Any]]:
    try:
        r = api_get("/health/ingestion", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=15)
def fetch_product_health() -> list[dict[str, Any]]:
    try:
        r = api_get("/health/products", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=15)
def fetch_computation_status() -> dict[str, Any] | None:
    try:
        r = api_get("/computation/status", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=15)
def fetch_pmn_accuracy_aggregate() -> dict[str, Any] | None:
    try:
        r = api_get("/analytics/pmn-accuracy", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ingestion Runs
# ---------------------------------------------------------------------------


@st.cache_data(ttl=10)
def fetch_ingestion_runs(
    source: str | None = None,
    status: str | None = None,
    product_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if source:
        params["source"] = source
    if status:
        params["status"] = status
    if product_id:
        params["product_id"] = product_id
    try:
        r = api_get("/ingestion/runs", params=params, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"runs": [], "total": 0, "page": page, "page_size": page_size}


@st.cache_data(ttl=10)
def fetch_ingestion_status() -> dict[str, Any] | None:
    try:
        r = api_get("/ingestion/status", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=10)
def fetch_queue_status() -> dict[str, Any] | None:
    try:
        r = api_get("/ingestion/queue/status", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@st.cache_data(ttl=30)
def fetch_audit_results(connector: str | None = None, days: int = 7) -> dict[str, Any] | None:
    params: dict[str, Any] = {"days": days}
    if connector:
        params["connector"] = connector
    try:
        r = api_get("/audit/connectors/results", params=params, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------


@st.cache_data(ttl=10)
def fetch_listing_detail(obs_id: int) -> dict[str, Any] | None:
    """Fetch full listing detail across all enrichment tables."""
    try:
        r = api_get(f"/listings/{obs_id}/detail", timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@st.cache_data(ttl=10)
def fetch_alert_rules() -> list[dict[str, Any]]:
    try:
        r = api_get("/alerts/rules", timeout=10.0)
        r.raise_for_status()
        return r.json().get("rules", [])
    except Exception:
        return []


@st.cache_data(ttl=10)
def fetch_alert_events(
    rule_id: str | None = None,
    product_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if rule_id:
        params["rule_id"] = rule_id
    if product_id:
        params["product_id"] = product_id
    try:
        r = api_get("/alerts/events", params=params, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {"events": [], "total": 0, "limit": limit, "offset": offset}


@st.cache_data(ttl=30)
def fetch_alert_precision(days: int = 30) -> dict[str, Any] | None:
    try:
        r = api_get("/analytics/alert-precision", params={"days": days}, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

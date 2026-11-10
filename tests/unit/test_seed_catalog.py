from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scripts import seed
from scripts.seed_catalogs import PILOT_LENSES


class FakeBackend:
    def __init__(self, products: list[dict[str, Any]] | None = None) -> None:
        self.categories: list[dict[str, str]] = []
        self.products = products or []
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if request.method == "GET" and path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and path == "/categories":
            return httpx.Response(200, json={"categories": self.categories})
        if request.method == "POST" and path == "/categories":
            category = {"category_id": f"cat-{len(self.categories)}", "name": body["name"]}
            self.categories.append(category)
            return httpx.Response(201, json=category)
        if request.method == "GET" and path == "/products":
            return httpx.Response(200, json={"products": self.products})
        if request.method == "POST" and path == "/products":
            product = {**body, "product_id": f"p-{len(self.products)}"}
            self.products.append(product)
            return httpx.Response(201, json=product)
        if request.method == "PUT" and path.startswith("/products/"):
            product_id = path.rsplit("/", 1)[1]
            product = next(p for p in self.products if p["product_id"] == product_id)
            product.update(body)
            return httpx.Response(200, json=product)
        if request.method == "GET" and path == "/alerts/rules":
            return httpx.Response(200, json={"rules": []})
        if request.method == "POST" and path == "/alerts/rules":
            return httpx.Response(201, json=body)
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="http://test", transport=httpx.MockTransport(self.handler))


def _writes(backend: FakeBackend) -> list[tuple[str, str]]:
    return [(method, path) for method, path, _ in backend.calls if method != "GET"]


def test_pilot_catalog_creates_inactive_lbc_ebay_products_with_default_interval() -> None:
    backend = FakeBackend()
    with backend.client() as client:
        seed.seed_catalog(client, "pilot-lenses", activate=False, scan_interval=None)

    assert len(backend.products) == len(PILOT_LENSES)
    for product in backend.products:
        assert product["is_active"] is False
        assert product["providers"] == ["leboncoin", "ebay"]
        assert product["ingestion_interval_minutes"] == 60
        assert product["enable_llm_validation"] is False
        assert "estimates" in product["description"]
    assert {c["name"] for c in backend.categories} == {"Photo Lenses"}


def test_pilot_catalog_is_idempotent() -> None:
    backend = FakeBackend()
    with backend.client() as client:
        seed.seed_catalog(client, "pilot-lenses", activate=False, scan_interval=None)
        writes_before = len(_writes(backend))
        seed.seed_catalog(client, "pilot-lenses", activate=False, scan_interval=None)

    assert len(_writes(backend)) == writes_before
    assert len(backend.products) == len(PILOT_LENSES)


def test_activate_flag_and_interval_create_active_products() -> None:
    backend = FakeBackend()
    with backend.client() as client:
        seed.seed_catalog(client, "pilot-lenses", activate=True, scan_interval=30)

    assert all(p["is_active"] is True for p in backend.products)
    assert all(p["ingestion_interval_minutes"] == 30 for p in backend.products)


def test_activate_flag_updates_existing_inactive_products_only_when_needed() -> None:
    backend = FakeBackend()
    with backend.client() as client:
        seed.seed_catalog(client, "pilot-lenses", activate=False, scan_interval=None)
        seed.seed_catalog(client, "pilot-lenses", activate=True, scan_interval=None)
        puts = [call for call in backend.calls if call[0] == "PUT"]
        seed.seed_catalog(client, "pilot-lenses", activate=True, scan_interval=None)

    assert len(puts) == len(PILOT_LENSES)
    assert all(body == {"is_active": True} for _, _, body in puts)
    assert [call for call in backend.calls if call[0] == "PUT"] == puts
    assert all(p["is_active"] is True for p in backend.products)


def test_rerun_without_flags_never_deactivates_or_overwrites_operator_edits() -> None:
    name = PILOT_LENSES[0].name
    edited = {
        "product_id": "p-edited",
        "name": name,
        "is_active": True,
        "ingestion_interval_minutes": 15,
        "price_min": 1234,
    }
    backend = FakeBackend(products=[edited])
    with backend.client() as client:
        seed.seed_catalog(client, "pilot-lenses", activate=False, scan_interval=None)

    assert edited == {
        "product_id": "p-edited",
        "name": name,
        "is_active": True,
        "ingestion_interval_minutes": 15,
        "price_min": 1234,
    }
    assert not [call for call in backend.calls if call[0] == "PUT"]


def test_default_seed_behaviour_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(seed, "_client", backend.client)
    monkeypatch.setattr("sys.argv", ["seed.py"])

    assert seed.main() == 0

    names = {p["name"] for p in backend.products}
    assert names == {p["name"] for p in seed.PRODUCTS}
    assert all(p["is_active"] is True for p in backend.products)
    assert all(p["providers"] == ["ebay", "leboncoin", "vinted"] for p in backend.products)
    assert ("POST", "/alerts/rules") in _writes(backend)


def test_catalog_cli_skips_default_products_and_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(seed, "_client", backend.client)
    monkeypatch.setattr("sys.argv", ["seed.py", "--catalog", "pilot-lenses"])

    assert seed.main() == 0

    assert {p["name"] for p in backend.products} == {p.name for p in PILOT_LENSES}
    assert ("POST", "/alerts/rules") not in _writes(backend)


@pytest.mark.parametrize(
    "argv",
    [
        ["seed.py", "--activate"],
        ["seed.py", "--scan-interval", "30"],
        ["seed.py", "--catalog", "pilot-lenses", "--scan-interval", "4"],
        ["seed.py", "--catalog", "unknown"],
    ],
)
def test_invalid_catalog_arguments_are_rejected(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as exc:
        seed.main()
    assert exc.value.code == 2

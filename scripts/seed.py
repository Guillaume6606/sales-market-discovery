"""Idempotent seeding of categories, product templates, and alert rules via the backend API.

Usage:
    uv run python scripts/seed.py                     # local backend (http://localhost:8000)
    API_URL=http://localhost:8000 python scripts/seed.py
    uv run python scripts/seed.py --products-only     # skip alert rules
    uv run python scripts/seed.py --rules-only        # skip products

Safe to re-run: existing categories/products/rules (matched by name) are left untouched.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx

API_URL = os.environ.get("API_URL", "http://localhost:8000")

# Starter set: liquid on eBay, 100-500 EUR, identifiable, condition-stable.
# See docs/BUSINESS_ROADMAP.md section 5 for the selection criteria.
WORDS_TO_AVOID = [
    "coque",
    "housse",
    "étui",
    "case",
    "panne",
    "hs",
    "pour pièces",
    "cassé",
    "fissuré",
    "broken",
    "repair",
    "réparer",
]

PRODUCTS: list[dict[str, Any]] = [
    {
        "name": "Sony WH-1000XM4",
        "category": "Audio",
        "search_query": "Sony WH-1000XM4",
        "brand": "Sony",
        "price_min": 80,
        "price_max": 250,
    },
    {
        "name": "Apple AirPods Pro 2",
        "category": "Audio",
        "search_query": "AirPods Pro 2",
        "brand": "Apple",
        "price_min": 90,
        "price_max": 230,
    },
    {
        "name": "Nintendo Switch OLED",
        "category": "Gaming",
        "search_query": "Nintendo Switch OLED",
        "brand": "Nintendo",
        "price_min": 120,
        "price_max": 280,
    },
    {
        "name": "Sony PlayStation 5",
        "category": "Gaming",
        "search_query": "PlayStation 5",
        "brand": "Sony",
        "price_min": 220,
        "price_max": 480,
        "extra_words_to_avoid": ["manette seule", "jeu", "jeux"],
    },
    {
        "name": "Tissot PRX",
        "category": "Watches",
        "search_query": "Tissot PRX",
        "brand": "Tissot",
        "price_min": 150,
        "price_max": 450,
        "extra_words_to_avoid": ["bracelet seul"],
    },
    {
        "name": "GoPro Hero 11 Black",
        "category": "Photography",
        "search_query": "GoPro Hero 11",
        "brand": "GoPro",
        "price_min": 120,
        "price_max": 300,
    },
]

ALERT_RULES: list[dict[str, Any]] = [
    {
        # Conservative starter rule: listing at least 25% below PMN
        # AND at least 30 EUR absolute margin. No seller-rating floor:
        # LBC/Vinted listings often have no rating and would be rejected.
        "name": "conservative-margin-25pct-30eur",
        "threshold_pct": -25.0,
        "min_margin_abs": 30.0,
        "channels": ["telegram"],
    },
]


def _ensure_categories(client: httpx.Client, names: set[str]) -> dict[str, str]:
    existing = {c["name"]: c["category_id"] for c in client.get("/categories").json()["categories"]}
    for name in sorted(names):
        if name in existing:
            print(f"  category exists: {name}")
            continue
        resp = client.post("/categories", json={"name": name})
        resp.raise_for_status()
        existing[name] = resp.json()["category_id"]
        print(f"  category created: {name}")
    return existing


def seed_products(client: httpx.Client) -> None:
    print("Seeding categories and products...")
    categories = _ensure_categories(client, {p["category"] for p in PRODUCTS})
    existing = {p["name"] for p in client.get("/products").json()["products"]}
    for product in PRODUCTS:
        if product["name"] in existing:
            print(f"  product exists: {product['name']}")
            continue
        payload = {
            "name": product["name"],
            "search_query": product["search_query"],
            "category_id": categories[product["category"]],
            "brand": product.get("brand"),
            "price_min": product.get("price_min"),
            "price_max": product.get("price_max"),
            "providers": product.get("providers", ["ebay", "leboncoin", "vinted"]),
            "words_to_avoid": WORDS_TO_AVOID + product.get("extra_words_to_avoid", []),
            "enable_llm_validation": False,
            "is_active": True,
        }
        resp = client.post("/products", json=payload)
        resp.raise_for_status()
        print(f"  product created: {product['name']}")


def seed_alert_rules(client: httpx.Client) -> None:
    print("Seeding alert rules...")
    existing = {r["name"] for r in client.get("/alerts/rules").json().get("rules", [])}
    for rule in ALERT_RULES:
        if rule["name"] in existing:
            print(f"  rule exists: {rule['name']}")
            continue
        resp = client.post("/alerts/rules", json=rule)
        resp.raise_for_status()
        print(f"  rule created: {rule['name']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products-only", action="store_true")
    parser.add_argument("--rules-only", action="store_true")
    args = parser.parse_args()

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        try:
            client.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            print(f"Backend not reachable at {API_URL}: {exc}", file=sys.stderr)
            return 1
        if not args.rules_only:
            seed_products(client)
        if not args.products_only:
            seed_alert_rules(client)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

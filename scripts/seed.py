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
    # Batch 2 (2026-07): 20 candidates ranked by absolute margin potential.
    # Substring matching on words_to_avoid: use multi-word phrases, never short tokens.
    {
        "name": "MacBook Air M1",
        "category": "Computers",
        "search_query": "MacBook Air M1",
        "brand": "Apple",
        "price_min": 250,
        "price_max": 600,
        "extra_words_to_avoid": ["icloud", "bloqué", "verrouillé", "qwerty"],
    },
    {
        "name": "MacBook Air M2",
        "category": "Computers",
        "search_query": "MacBook Air M2",
        "brand": "Apple",
        "price_min": 350,
        "price_max": 800,
        "extra_words_to_avoid": ["icloud", "bloqué", "verrouillé", "qwerty"],
    },
    {
        "name": "Fujifilm X100V",
        "category": "Photography",
        "search_query": "Fujifilm X100V",
        "brand": "Fujifilm",
        "price_min": 600,
        "price_max": 1300,
        "extra_words_to_avoid": ["x100vi", "x100f", "x100s", "x100t"],
    },
    {
        "name": "Apple iPhone 14",
        "category": "Smartphones",
        "search_query": "iPhone 14",
        "brand": "Apple",
        "price_min": 200,
        "price_max": 500,
        "extra_words_to_avoid": ["14 pro", "14 plus", "icloud", "bloqué", "verrouillé"],
    },
    {
        "name": "DJI Mini 4 Pro",
        "category": "Drones",
        "search_query": "DJI Mini 4 Pro",
        "brand": "DJI",
        "price_min": 300,
        "price_max": 750,
        "extra_words_to_avoid": ["mini 2", "mini 3", "crash"],
    },
    {
        "name": "Steam Deck OLED",
        "category": "Gaming",
        "search_query": "Steam Deck OLED",
        "brand": "Valve",
        "price_min": 250,
        "price_max": 550,
        "extra_words_to_avoid": ["lcd"],
    },
    {
        "name": "Apple Watch Ultra 2",
        "category": "Watches",
        "search_query": "Apple Watch Ultra 2",
        "brand": "Apple",
        "price_min": 300,
        "price_max": 650,
        "extra_words_to_avoid": ["bracelet seul", "icloud", "verrouillé"],
    },
    {
        "name": "Bambu Lab P1S",
        "category": "3D Printing",
        "search_query": "Bambu Lab P1S",
        "brand": "Bambu Lab",
        "price_min": 250,
        "price_max": 600,
    },
    {
        "name": "Nintendo Switch 2",
        "category": "Gaming",
        "search_query": "Nintendo Switch 2",
        "brand": "Nintendo",
        "price_min": 250,
        "price_max": 500,
        "extra_words_to_avoid": ["oled", "lite", "manette seule", "jeu", "jeux"],
    },
    {
        "name": "Nvidia RTX 4070",
        "category": "Computers",
        "search_query": "RTX 4070",
        "brand": "Nvidia",
        "price_min": 250,
        "price_max": 600,
        "extra_words_to_avoid": ["4070 ti", "4070ti", "4070 super", "pc gamer", "unité centrale", "ordinateur"],
    },
    {
        "name": "Garmin Fenix 7",
        "category": "Watches",
        "search_query": "Garmin Fenix 7",
        "brand": "Garmin",
        "price_min": 200,
        "price_max": 500,
        "extra_words_to_avoid": ["fenix 7x", "fenix 7s", "bracelet seul"],
    },
    {
        "name": "Technics SL-1200",
        "category": "Audio",
        "search_query": "Technics SL-1200",
        "brand": "Technics",
        "price_min": 250,
        "price_max": 900,
        "extra_words_to_avoid": ["cellule seule"],
    },
    {
        "name": "Apple iPad Air 5",
        "category": "Tablets",
        "search_query": "iPad Air 5",
        "brand": "Apple",
        "price_min": 250,
        "price_max": 550,
        "extra_words_to_avoid": ["icloud", "bloqué", "verrouillé"],
    },
    {
        "name": "Dyson V15 Detect",
        "category": "Home",
        "search_query": "Dyson V15",
        "brand": "Dyson",
        "price_min": 180,
        "price_max": 500,
        "extra_words_to_avoid": ["batterie seule", "batterie hs"],
    },
    {
        "name": "Dyson Airwrap",
        "category": "Home",
        "search_query": "Dyson Airwrap",
        "brand": "Dyson",
        "price_min": 180,
        "price_max": 450,
        "extra_words_to_avoid": ["embout seul", "accessoire seul"],
    },
    {
        "name": "Apple AirPods Max",
        "category": "Audio",
        "search_query": "AirPods Max",
        "brand": "Apple",
        "price_min": 180,
        "price_max": 450,
    },
    {
        "name": "Meta Quest 3",
        "category": "Gaming",
        "search_query": "Meta Quest 3",
        "brand": "Meta",
        "price_min": 200,
        "price_max": 480,
        "extra_words_to_avoid": ["quest 2", "quest 3s"],
    },
    {
        "name": "Tamron 28-75 f/2.8 Sony E",
        "category": "Photography",
        "search_query": "Tamron 28-75",
        "brand": "Tamron",
        "price_min": 250,
        "price_max": 600,
        "extra_words_to_avoid": ["nikon"],
    },
    {
        "name": "Sonos Beam Gen 2",
        "category": "Audio",
        "search_query": "Sonos Beam",
        "brand": "Sonos",
        "price_min": 150,
        "price_max": 450,
    },
    {
        "name": "Seiko Presage Cocktail Time",
        "category": "Watches",
        "search_query": "Seiko Presage Cocktail",
        "brand": "Seiko",
        "price_min": 180,
        "price_max": 550,
        "extra_words_to_avoid": ["bracelet seul"],
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

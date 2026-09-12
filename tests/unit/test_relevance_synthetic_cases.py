"""Additional synthetic regressions; these are not production precision data."""

import pytest

from ingestion.relevance import RelevanceClass, classify_listing_relevance


@pytest.mark.parametrize(
    ("target", "query", "listing", "expected"),
    [
        (
            "Nintendo Switch OLED",
            "Nintendo Switch OLED",
            "Nintendo Switch OLED blanche + Mario Kart",
            RelevanceClass.DEVICE_BUNDLE,
        ),
        (
            "Nintendo Switch OLED",
            "Nintendo Switch OLED",
            "Station d'accueil dock pour Switch OLED",
            RelevanceClass.ACCESSORY,
        ),
        (
            "DJI Mini 4 Pro",
            "DJI Mini 4 Pro",
            "Drone DJI Mini 4 Pro crashé pour réparation",
            RelevanceClass.PARTS_BROKEN,
        ),
        (
            "Apple AirPods Pro 2",
            "AirPods Pro 2",
            "Boîtier de charge seul AirPods Pro 2 USB-C",
            RelevanceClass.ACCESSORY,
        ),
        (
            "Sony WH-1000XM4",
            "Sony WH-1000XM4",
            "Casque Sony WH-1000XM5 noir",
            RelevanceClass.WRONG_VARIANT,
        ),
        (
            "Tissot PRX",
            "Tissot PRX",
            "Bracelet acier compatible Tissot PRX",
            RelevanceClass.ACCESSORY,
        ),
    ],
)
def test_synthetic_heldout_relevance_cases(
    target: str, query: str, listing: str, expected: RelevanceClass
) -> None:
    assert classify_listing_relevance(target, query, listing).classification is expected

"""Frozen synthetic holdout evaluated after classifier development."""

import pytest

from ingestion.relevance import RelevanceClass, classify_listing_relevance


@pytest.mark.parametrize(
    ("target", "query", "listing", "expected"),
    [
        (
            "Fujifilm X100V",
            "Fujifilm X100V",
            "Appareil photo Fujifilm X100V noir",
            RelevanceClass.EXACT_DEVICE,
        ),
        (
            "Fujifilm X100V",
            "Fujifilm X100V",
            "Appareil photo Fujifilm X100VI silver",
            RelevanceClass.WRONG_VARIANT,
        ),
        (
            "Steam Deck OLED",
            "Steam Deck OLED",
            "Console Valve Steam Deck LCD 512GB",
            RelevanceClass.UNCERTAIN,
        ),
        (
            "Apple Watch Ultra 2",
            "Apple Watch Ultra 2",
            "Montre Apple Watch Ultra 2 avec bracelet sport",
            RelevanceClass.DEVICE_BUNDLE,
        ),
        (
            "MacBook Air M1",
            "MacBook Air M1",
            "MacBook Air M1 pour pièces, écran cassé",
            RelevanceClass.PARTS_BROKEN,
        ),
        (
            "DJI Mini 4 Pro",
            "DJI Mini 4 Pro",
            "Drone DJI Mini 4 Pro Fly More Combo",
            RelevanceClass.EXACT_DEVICE,
        ),
    ],
)
def test_frozen_synthetic_holdout(
    target: str, query: str, listing: str, expected: RelevanceClass
) -> None:
    assert classify_listing_relevance(target, query, listing).classification is expected

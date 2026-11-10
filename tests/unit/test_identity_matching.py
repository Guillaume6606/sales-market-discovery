from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from ingestion.filtering import _matches_words_to_avoid
from ingestion.relevance import RelevanceClass, classify_listing_relevance, text_tokens
from ingestion.valuation import _tokens


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Sony 24-70mm", {"24", "70"}),
        ("Sony 24-70 mm", {"24", "70"}),
        ("Sony 24–70mm", {"24", "70"}),
        ("Canon RF 24-70 f/2.8L", {"f2p8", "l"}),
        ("Canon RF 24-105 f/4L", {"f4", "l"}),
        ("Canon RF 24-70 F2.8", {"f2p8"}),
        ("Fuji XF 16-55 2,8", {"2", "8"}),
        ("Nikon Z 50mm f/1.2 S", {"f1p2", "s"}),
    ],
)
def test_shared_tokens(title: str, expected: set[str]) -> None:
    assert expected <= text_tokens(title)
    assert _tokens(title) == text_tokens(title)


def test_aperture_followed_by_punctuation_keeps_its_token() -> None:
    assert "f4" in text_tokens("Sony 70-200 f4. Etat neuf")


def test_valuation_reference_requiring_second_focal_matches_glued_unit() -> None:
    assert {"sony", "24", "70", "gm", "ii"} <= _tokens("Sony FE 24-70mm f/2.8 GM II")


def _explicit(title: str, required: tuple[str, ...], excluded: tuple[str, ...] = ()) -> Any:
    return classify_listing_relevance("ignored name", "ignored query", title, required, excluded)


def test_required_tokens_replace_inferred_aperture() -> None:
    decision = _explicit("Fuji XF 16-55", ("xf", "16", "55"))
    assert decision.classification is RelevanceClass.EXACT_DEVICE


def test_single_letter_required_token_is_enforced() -> None:
    required = ("nikon", "z", "24", "120")
    assert not _explicit("Nikon 24-120 f/4 G ED VR", required).is_relevant
    assert _explicit("Nikon Z 24-120 f/4 S", required).is_relevant


def test_exclusion_is_by_whole_token_not_substring() -> None:
    required = ("tamron", "28", "75", "g2", "sony")
    assert _explicit("Tamron 28-75 Di III VXD G2 Sony E", required, ("ii",)).is_relevant
    rejected = _explicit("Sony FE 24-70 GM II", ("sony", "24", "70", "gm"), ("ii",))
    assert rejected.classification is RelevanceClass.WRONG_VARIANT


@pytest.mark.parametrize(
    ("title", "required", "excluded", "relevant"),
    [
        ("Nikkor Z 24-120 f4 S", ("nikon", "z", "24", "120"), (), True),
        ("Sony 24-70 GM2 f/2.8", ("sony", "24", "70", "gm", "ii"), (), True),
        ("Sony 24-70 GM2 f/2.8", ("sony", "24", "70", "gm"), ("ii",), False),
        ("Fujinon XF 16-55", ("fujifilm", "xf", "16", "55"), (), True),
    ],
)
def test_aliases(
    title: str, required: tuple[str, ...], excluded: tuple[str, ...], relevant: bool
) -> None:
    assert _explicit(title, required, excluded).is_relevant is relevant


def test_bare_decimal_is_an_aperture_only_in_explicit_identity() -> None:
    required = ("canon", "rf", "70", "200", "f4")
    assert not _explicit("Canon RF 70-200 2.8 L", required, ("f2p8",)).is_relevant
    assert not _explicit("Canon RF 70-200 F4 + TC 1.4x", required).is_relevant
    assert "f1p4" not in text_tokens("Nikon TC 1.4x")


def test_explicit_identity_rejects_bundles_and_accessories() -> None:
    required = ("nikon", "z", "70", "200")
    assert not _explicit("Nikon Z 70-200 f/2.8 VR S + TC 1.4x", required).is_relevant
    assert not _explicit("Collier Nikon pour Z 70-200", required).is_relevant
    assert not _explicit("Pare-soleil Nikon Z 70-200", required).is_relevant


def test_mount_name_is_not_a_mount_accessory() -> None:
    required = ("sigma", "24", "70", "sony")
    assert _explicit("Sigma 24-70 F2.8 DG DN Art pour Sony E-mount", required).is_relevant


def test_variant_terms_are_not_inferred_with_explicit_identity() -> None:
    required = ("sony", "70", "200", "g")
    assert _explicit("Sony 70-200mm f/4 G OSS objectif pro", required).is_relevant
    legacy = classify_listing_relevance(
        "Sony FE 70-200mm f/4 G OSS", "Sony 70-200 F4 G", "Sony 70-200mm f/4 G OSS objectif pro"
    )
    assert legacy.classification is RelevanceClass.WRONG_VARIANT


def test_empty_required_tokens_use_the_inferred_identity() -> None:
    args = ("Nikon Z 24-120mm f/4 S", "Nikon Z 24-120", "Nikon 24-120 f/4 G ED VR")
    assert classify_listing_relevance(*args) == classify_listing_relevance(*args, (), ("g",))


def _avoid(title: str, words: list[str], required: list[str]) -> bool:
    snapshot = SimpleNamespace(words_to_avoid=words, required_tokens=required)
    return _matches_words_to_avoid(cast(Any, snapshot), cast(Any, SimpleNamespace(title=title)))


@pytest.mark.parametrize(
    ("title", "passes"),
    [
        ("Sony 24-70 GM II sans rayure ni champignon", True),
        ("Sony 24-70 GM II sans champignon", True),
        ("Sony 24-70 GM II pas de champignon", True),
        ("Sony 24-70 GM II champignon", False),
        ("Sony 24-70 GM II sans boîte, champignon dans la lentille", False),
    ],
)
def test_words_to_avoid_negation_with_explicit_identity(title: str, passes: bool) -> None:
    assert _avoid(title, ["champignon"], ["sony"]) is passes


def test_words_to_avoid_negation_is_ignored_without_identity_tokens() -> None:
    assert _avoid("Sony 24-70 GM II sans champignon", ["champignon"], []) is False

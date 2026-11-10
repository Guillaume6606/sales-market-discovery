"""Title filtering of the pilot lens catalog against hand-labelled marketplace titles.

``SHOULD_ACCEPT`` is the operator truth (exact reference, working, sold as a lens
with its own caps/hood/box only), written before running the filter. It is the
DEVELOPMENT set: the catalog identity tokens were tuned on it. The untouched held-out
set is ``pilot_heldout_titles.HELD_OUT``.

``LEGACY_KNOWN_ERRORS`` records where the stages disagree for a product without
``required_tokens``; ``IDENTITY_KNOWN_ERRORS`` with the catalog identity tokens.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ingestion.connectors.ebay import _extract_brand_from_title
from ingestion.filtering import (
    _matches_brand,
    _matches_price,
    _matches_words_to_avoid,
)
from ingestion.relevance import classify_listing_relevance
from ingestion.schemas import ProductTemplateSnapshot
from ingestion.valuation import _reference_reasons
from libs.common.models import Listing
from scripts.seed_catalogs import PILOT_LENSES, CatalogProduct

Case = tuple[str, float, bool]

SHOULD_ACCEPT: dict[str, list[Case]] = {
    "Sony FE 24-70mm f/2.8 GM II": [
        ("Sony FE 24-70mm f/2.8 GM II", 1650, True),
        ("Objectif Sony 24-70 GM II SEL2470GM2 comme neuf", 1700, True),
        ("Sony FE 24-70mm F2.8 GM", 1000, False),
        ("Pare-soleil Sony ALC-SH172 pour 24-70 GM II", 45, False),
        ("Sony A7 IV + 24-70 f2.8 GM II", 3200, False),
        ("Sony 24-70 GM2 f/2.8 parfait état", 1600, True),
        ("Sony FE 24-70 f/2.8 GM II sans rayure ni champignon", 1700, True),
    ],
    "Sony FE 24-70mm f/2.8 GM": [
        ("Sony FE 24-70mm f/2.8 GM SEL2470GM", 950, True),
        ("Objectif Sony 24-70 GM f2.8 très bon état", 900, True),
        ("Sony FE 24-70mm f/2.8 GM II", 1650, False),
        ("Sony FE 24-70 F2.8 GM Mark II", 1600, False),
        ("Sony FE 24-70mm f/4 ZA OSS Zeiss", 450, False),
        ("Sigma 24-70mm f/2.8 DG DN Art monture Sony E", 700, False),
        ("Objectif Sony 24-70 GM pour pièces", 300, False),
    ],
    "Sony FE 70-200mm f/4 G OSS": [
        ("Sony FE 70-200mm f/4 G OSS", 650, True),
        ("Sony 70-200 F4 G OSS SEL70200G", 700, True),
        ("Sony FE 70-200mm f/4 Macro G OSS II", 1300, False),
        ("Sony FE 70-200 f/2.8 GM OSS", 1300, False),
        ("Collier de pied Sony pour 70-200 f4", 60, False),
        ("Sony 70-200mm f/4 G OSS objectif pro état neuf", 750, True),
        ("Lot Sony 70-200 f4 G + 24-105 G", 1300, False),
    ],
    "Canon RF 24-70mm f/2.8 L IS USM": [
        ("Canon RF 24-70mm F2.8 L IS USM", 1600, True),
        ("Objectif Canon RF 24-70 f/2.8L IS USM", 1550, True),
        ("Canon EF 24-70mm f/2.8L II USM", 900, False),
        ("Canon EOS R6 + RF 24-70 2.8", 2500, False),
        ("Bouchon Canon RF 24-70", 15, False),
        ("Canon RF 24-70 f2.8 L champignon", 800, False),
        ("Canon RF 24-70mm f/2.8 L IS USM avec pare-soleil", 1500, True),
    ],
    "Canon RF 24-105mm f/4 L IS USM": [
        ("Canon RF 24-105mm F4 L IS USM", 750, True),
        ("Canon RF 24-105 f/4L IS USM", 700, True),
        ("Canon RF 24-105mm F4-7.1 IS STM", 250, False),
        ("Canon EF 24-105mm f/4L IS II USM", 550, False),
        ("Canon RF 24-105mm F2.8 L IS USM Z", 2500, False),
        ("Canon EOS RP + RF 24-105 f4 L", 1200, False),
        ("Canon RF 24-105 f4 L rayé lentille frontale", 500, False),
    ],
    "Nikon Z 24-120mm f/4 S": [
        ("Nikon Z 24-120mm f/4 S", 750, True),
        ("Nikkor Z 24-120 f4 S comme neuf", 800, True),
        ("Nikon 24-120 f/4 G ED VR", 520, False),
        ("Nikon Z6 II + Z 24-120 f/4 S", 1300, False),
        ("Pare-soleil HB-102 Nikon Z 24-120", 30, False),
        ("Nikon Z 24-120 f4 S HS", 300, False),
    ],
    "Nikon Z 70-200mm f/2.8 VR S": [
        ("Nikon Z 70-200mm f/2.8 VR S", 2000, True),
        ("Nikkor Z 70-200 2.8 VR S", 2000, True),
        ("Nikon AF-S 70-200mm f/2.8E FL ED VR", 1500, False),
        ("Nikon 70-200 f/2.8 VR II", 1300, False),
        ("Collier Nikon pour Z 70-200", 80, False),
        ("Nikon Z 70-200 f/2.8 VR S + TC 1.4x", 2600, False),
    ],
    "Fujifilm XF 16-55mm f/2.8 R LM WR": [
        ("Fujifilm XF 16-55mm f/2.8 R LM WR", 650, True),
        ("Fuji XF 16-55 2.8", 600, True),
        ("Fujifilm XF 16-55mm F2.8 R LM WR II", 1100, False),
        ("Fujifilm X-T4 + XF 16-55 f2.8", 1200, False),
        ("Fujinon XF 16-80mm f/4 R OIS WR", 450, False),
        ("Fujifilm XF 16-55 f/2.8 pour pièces", 200, False),
    ],
    "Sigma 24-70mm f/2.8 DG DN Art (Sony E)": [
        ("Sigma 24-70mm f/2.8 DG DN Art Sony E", 750, True),
        ("Sigma 24-70 F2.8 DG DN Art pour Sony E-mount", 700, True),
        ("Sigma 24-70mm f/2.8 DG DN II Art Sony E", 1100, False),
        ("Sigma 24-70mm f/2.8 DG DN Art monture L", 700, False),
        ("Sigma 24-70mm f/2.8 DG OS HSM Art Canon EF", 600, False),
        ("Sigma 24-70 DG DN Art Sony + Sony A7 III", 1300, False),
    ],
    "Tamron 28-75mm f/2.8 Di III VXD G2 (Sony E)": [
        ("Tamron 28-75mm f/2.8 Di III VXD G2 Sony E", 600, True),
        ("Tamron 28-75 G2 Sony FE", 580, True),
        ("Tamron 28-75mm f/2.8 Di III RXD Sony E", 450, False),
        ("Tamron 28-75mm f/2.8 Di III VXD G2 Nikon Z", 600, False),
        ("Pare-soleil Tamron 28-75 G2", 25, False),
        ("Tamron 28-75 f2.8 G2 lentille rayée", 420, False),
    ],
}

_NO_APERTURE = "FN: aperture from the product name is required but the title omits it"
LEGACY_KNOWN_ERRORS: dict[tuple[str, str], str] = {
    (
        "Sony FE 24-70mm f/2.8 GM II",
        "Objectif Sony 24-70 GM II SEL2470GM2 comme neuf",
    ): _NO_APERTURE,
    (
        "Sony FE 24-70mm f/2.8 GM II",
        "Sony 24-70 GM2 f/2.8 parfait état",
    ): "FN: 'GM2' spelling is not the 'gm'+'ii' tokens of the search query",
    (
        "Sony FE 24-70mm f/2.8 GM II",
        "Sony FE 24-70 f/2.8 GM II sans rayure ni champignon",
    ): "FN: substring words_to_avoid ignores negation ('sans champignon')",
    (
        "Sony FE 70-200mm f/4 G OSS",
        "Sony 70-200mm f/4 G OSS objectif pro état neuf",
    ): "FN: generic variant term 'pro' is read as another model variant",
    (
        "Nikon Z 24-120mm f/4 S",
        "Nikkor Z 24-120 f4 S comme neuf",
    ): "FN: 'Nikkor' title without 'Nikon' misses the query token",
    (
        "Nikon Z 24-120mm f/4 S",
        "Nikon 24-120 f/4 G ED VR",
    ): "FP: F-mount lens; the single-letter mount token 'z' is never required",
    (
        "Nikon Z 70-200mm f/2.8 VR S",
        "Nikkor Z 70-200 2.8 VR S",
    ): "FN: 'Nikkor' title without 'Nikon' misses the query token",
    (
        "Nikon Z 70-200mm f/2.8 VR S",
        "Nikon Z 70-200 f/2.8 VR S + TC 1.4x",
    ): "FP: lens + teleconverter bundle is accepted as a device bundle",
    ("Fujifilm XF 16-55mm f/2.8 R LM WR", "Fuji XF 16-55 2.8"): _NO_APERTURE,
    (
        "Sigma 24-70mm f/2.8 DG DN Art (Sony E)",
        "Sigma 24-70 F2.8 DG DN Art pour Sony E-mount",
    ): "FN: 'pour ... E-mount' is classified as a mount accessory",
    ("Tamron 28-75mm f/2.8 Di III VXD G2 (Sony E)", "Tamron 28-75 G2 Sony FE"): _NO_APERTURE,
}

IDENTITY_KNOWN_ERRORS: dict[tuple[str, str], str] = {}

CATALOG = {product.name: product for product in PILOT_LENSES}


def _snapshot(product: CatalogProduct, identity: bool = False) -> ProductTemplateSnapshot:
    return ProductTemplateSnapshot(
        product_id="00000000-0000-0000-0000-000000000001",
        name=product.name,
        description=product.description(),
        search_query=product.search_query,
        category_id="00000000-0000-0000-0000-000000000010",
        category_name=product.category,
        brand=product.brand,
        price_min=float(product.price_min),
        price_max=float(product.price_max),
        providers=["leboncoin", "ebay"],
        words_to_avoid=list(product.words_to_avoid),
        enable_llm_validation=False,
        is_active=True,
        required_tokens=list(product.required_tokens) if identity else [],
        excluded_tokens=list(product.excluded_tokens) if identity else [],
    )


def _listing(title: str, price: float) -> Listing:
    return Listing(
        source="leboncoin",
        listing_id="t",
        title=title,
        price=price,
        currency="EUR",
        condition_raw=None,
        condition_norm=None,
        location=None,
        seller_rating=None,
        shipping_cost=None,
        observed_at=datetime.now(UTC),
        is_sold=False,
        url="https://www.leboncoin.fr/ad/1",
    )


def filter_accepts(
    product: CatalogProduct,
    title: str,
    price: float,
    source: str = "leboncoin",
    identity: bool = True,
) -> bool:
    """Price, brand, relevance and words-to-avoid stages of ``filter_listings_multi_stage``."""
    snapshot = _snapshot(product, identity)
    listing = _listing(title, price)
    if source == "ebay":
        listing = listing.model_copy(
            update={"source": "ebay", "brand": _extract_brand_from_title(title)}
        )
    return (
        _matches_price(snapshot, listing)
        and _matches_brand(snapshot, listing)
        and classify_listing_relevance(
            snapshot.name,
            snapshot.search_query,
            title,
            snapshot.required_tokens,
            snapshot.excluded_tokens,
        ).is_relevant
        and _matches_words_to_avoid(snapshot, listing)
    )


def valuation_accepts(product: CatalogProduct, title: str) -> bool:
    """Identity checks of the valuation gate, with the catalog's suggested reference tokens."""
    reference = SimpleNamespace(
        required_tokens=list(product.reference_tokens),
        excluded_tokens=list(product.reference_excluded_tokens),
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
    return not any(
        reason.startswith("relevance_")
        or reason in {"required_tokens_missing", "excluded_tokens_present"}
        for reason in reasons
    )


CASES = [
    (name, title, price, expected)
    for name, cases in SHOULD_ACCEPT.items()
    for title, price, expected in cases
]


def _error_counts(identity: bool) -> tuple[tuple[int, int], tuple[int, int]]:
    positives = [case for case in CASES if case[3]]
    negatives = [case for case in CASES if not case[3]]
    false_negatives = [
        case
        for case in positives
        if not filter_accepts(CATALOG[case[0]], *case[1:3], identity=identity)
    ]
    false_positives = [
        case
        for case in negatives
        if filter_accepts(CATALOG[case[0]], *case[1:3], identity=identity)
    ]
    return (len(false_negatives), len(positives)), (len(false_positives), len(negatives))


def test_labelled_references_exist_in_catalog() -> None:
    assert len(SHOULD_ACCEPT) >= 8
    assert set(SHOULD_ACCEPT) <= set(CATALOG)


@pytest.mark.parametrize(("name", "title", "price", "expected"), CASES)
def test_legacy_filter_without_identity_tokens_is_unchanged(
    name: str, title: str, price: float, expected: bool
) -> None:
    """Products without ``required_tokens`` keep the pre-0015 decisions on the dev set.

    The only differences are the two 'f/2.8L' / 'f/4L' tokenization fixes.
    """
    accepted = filter_accepts(CATALOG[name], title, price, identity=False)
    if (name, title) in LEGACY_KNOWN_ERRORS:
        assert accepted is not expected, LEGACY_KNOWN_ERRORS[(name, title)]
    else:
        assert accepted is expected


def test_legacy_error_rates_with_denominators() -> None:
    assert _error_counts(identity=False) == ((9, 24), (2, 41))


@pytest.mark.parametrize(("name", "title", "price", "expected"), CASES)
def test_identity_filter_against_truth(name: str, title: str, price: float, expected: bool) -> None:
    accepted = filter_accepts(CATALOG[name], title, price)
    if (name, title) in IDENTITY_KNOWN_ERRORS:
        assert accepted is not expected, IDENTITY_KNOWN_ERRORS[(name, title)]
    else:
        assert accepted is expected


def test_identity_error_rates_with_denominators() -> None:
    assert _error_counts(identity=True) == ((0, 24), (0, 41))


def test_single_letter_mount_token_in_search_query_is_not_required() -> None:
    nikon_z = CATALOG["Nikon Z 24-120mm f/4 S"]
    decision = classify_listing_relevance(
        nikon_z.name, nikon_z.search_query, "Nikon 24-120 f/4 G ED VR"
    )
    assert decision.is_relevant


def test_words_to_avoid_substring_ignores_negation() -> None:
    snapshot = _snapshot(CATALOG["Sony FE 24-70mm f/2.8 GM II"])
    clean = _listing("Sony FE 24-70 f/2.8 GM II sans champignon", 1700)
    assert _matches_words_to_avoid(snapshot, clean) is False


def test_ebay_title_brand_for_sony_mount_does_not_reject_third_party_lens() -> None:
    sigma = _snapshot(CATALOG["Sigma 24-70mm f/2.8 DG DN Art (Sony E)"])
    title = "Sigma 24-70mm f/2.8 DG DN Art Sony E"
    listing = _listing(title, 750).model_copy(
        update={"source": "ebay", "brand": _extract_brand_from_title(title)}
    )
    assert listing.brand == "Sony"
    assert _matches_brand(sigma, listing) is True


def test_provider_brand_absent_from_title_still_rejects() -> None:
    sigma = _snapshot(CATALOG["Sigma 24-70mm f/2.8 DG DN Art (Sony E)"])
    listing = _listing("Sony FE 24-70mm f/2.8 GM", 900).model_copy(update={"brand": "Sony"})
    assert _matches_brand(sigma, listing) is False
    tamron_titled = _listing("Objectif 24-70 Sony E", 900).model_copy(update={"brand": "Tamron"})
    assert _matches_brand(sigma, tamron_titled) is False


def test_catalog_shape() -> None:
    assert 25 <= len(PILOT_LENSES) <= 40
    assert len({product.name for product in PILOT_LENSES}) == len(PILOT_LENSES)
    for product in PILOT_LENSES:
        assert 0 < product.price_min < product.price_max
        assert product.reference_tokens
        assert "hs" not in product.words_to_avoid
        lowered = product.name.casefold()
        assert not any(word.casefold() in lowered for word in product.words_to_avoid), product.name


@pytest.mark.parametrize("identity", [False, True])
def test_every_catalog_name_is_accepted_by_its_own_filter(identity: bool) -> None:
    rejected = [
        product.name
        for product in PILOT_LENSES
        if not filter_accepts(
            product, product.name, (product.price_min + product.price_max) / 2, identity=identity
        )
    ]
    assert rejected == []


def test_every_catalog_product_has_identity_tokens() -> None:
    for product in PILOT_LENSES:
        assert product.required_tokens, product.name
        assert not set(product.required_tokens) & set(product.excluded_tokens), product.name


def test_neighbouring_catalog_names_reject_each_other() -> None:
    accepted_pairs = [
        (product.name, other.name)
        for product in PILOT_LENSES
        for other in PILOT_LENSES
        if other is not product
        and filter_accepts(product, other.name, (product.price_min + product.price_max) / 2)
    ]
    assert accepted_pairs == []

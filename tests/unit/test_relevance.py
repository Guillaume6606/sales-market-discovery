import pytest

from ingestion.relevance import RelevanceClass, classify_listing_relevance


@pytest.mark.parametrize(
    ("listing_title", "expected"),
    [
        ("Console Sony PS5 standard avec lecteur", RelevanceClass.EXACT_DEVICE),
        ("PS5 avec 2 manettes et 4 jeux", RelevanceClass.DEVICE_BUNDLE),
        ("Manette + console PS5 avec lecteur", RelevanceClass.DEVICE_BUNDLE),
        ("Console PS5 avec manette pour jouer", RelevanceClass.DEVICE_BUNDLE),
        ("Manette DualSense sans fil pour PS5", RelevanceClass.ACCESSORY),
        ("Lot de 3 jeux PS5", RelevanceClass.ACCESSORY),
        ("PS5 pour pièces, ne s'allume plus", RelevanceClass.PARTS_BROKEN),
        ("Console PS5 Slim édition digitale", RelevanceClass.WRONG_VARIANT),
        ("Console PS5 Pro + manette", RelevanceClass.WRONG_VARIANT),
        ("Lot PS5 et Xbox Series S", RelevanceClass.UNCERTAIN),
    ],
)
def test_classifies_playstation_results(listing_title: str, expected: RelevanceClass) -> None:
    decision = classify_listing_relevance(
        target_title="Sony PlayStation 5",
        search_query="PlayStation 5",
        listing_title=listing_title,
    )

    assert decision.classification is expected
    assert decision.is_relevant is (
        expected in {RelevanceClass.EXACT_DEVICE, RelevanceClass.DEVICE_BUNDLE}
    )


@pytest.mark.parametrize(
    ("listing_title", "expected"),
    [
        ("GoPro Hero 11 Black caméra d'action", RelevanceClass.EXACT_DEVICE),
        ("GoPro Hero 11 avec 3 batteries et fixation", RelevanceClass.DEVICE_BUNDLE),
        ("Batteries compatibles pour GoPro Hero 11", RelevanceClass.ACCESSORY),
        ("Boîtier étanche GoPro Hero 11 uniquement", RelevanceClass.ACCESSORY),
        ("GoPro Hero 10 Black", RelevanceClass.WRONG_VARIANT),
        ("Caméra sportive GoPro en bon état", RelevanceClass.UNCERTAIN),
    ],
)
def test_classifies_gopro_results(listing_title: str, expected: RelevanceClass) -> None:
    decision = classify_listing_relevance(
        target_title="GoPro Hero 11 Black",
        search_query="GoPro Hero 11",
        listing_title=listing_title,
    )

    assert decision.classification is expected


def test_lens_target_accepts_a_lens_as_the_product() -> None:
    decision = classify_listing_relevance(
        target_title="Tamron 28-75 f/2.8 Sony E",
        search_query="Tamron 28-75",
        listing_title="Objectif Tamron 28-75mm f/2.8 Di III VXD G2 Sony E",
    )

    assert decision.classification is RelevanceClass.EXACT_DEVICE
    assert decision.is_relevant is True


def test_lens_target_rejects_a_different_focal_length() -> None:
    decision = classify_listing_relevance(
        target_title="Tamron 28-75 f/2.8 Sony E",
        search_query="Tamron 28-75",
        listing_title="Objectif Tamron 70-180mm f/2.8 Sony E",
    )

    assert decision.classification is RelevanceClass.WRONG_VARIANT
    assert decision.is_relevant is False


def test_lens_target_rejects_lens_cap_only() -> None:
    decision = classify_listing_relevance(
        target_title="Tamron 28-75 f/2.8 Sony E",
        search_query="Tamron 28-75",
        listing_title="Bouchon objectif compatible Tamron 28-75mm",
    )

    assert decision.classification is RelevanceClass.ACCESSORY


def test_lens_target_requires_aperture_omitted_from_broad_query() -> None:
    decision = classify_listing_relevance(
        target_title="Canon RF 24-70mm F2.8",
        search_query="Canon RF 24-70mm",
        listing_title="Objectif Canon RF 24-70mm F4",
    )

    assert decision.classification is RelevanceClass.WRONG_VARIANT


def test_rejects_a_different_storage_variant() -> None:
    decision = classify_listing_relevance(
        target_title="Apple iPhone 14 Pro 128GB",
        search_query="iPhone 14 Pro 128GB",
        listing_title="iPhone 14 Pro 256GB noir",
    )

    assert decision.classification is RelevanceClass.WRONG_VARIANT


@pytest.mark.parametrize(
    ("target", "query", "listing"),
    [
        ("Apple iPhone 14 Pro 128GB", "iPhone 14", "iPhone 14 256GB noir"),
        ("Sony PlayStation 5 Slim", "PlayStation 5", "Console PS5 avec lecteur"),
    ],
)
def test_target_discriminators_are_required_when_query_is_broad(
    target: str, query: str, listing: str
) -> None:
    assert (
        classify_listing_relevance(target, query, listing).classification
        is RelevanceClass.WRONG_VARIANT
    )


def test_generation_in_target_is_required_when_query_omits_it() -> None:
    decision = classify_listing_relevance(
        "Sonos Beam Gen 2",
        "Sonos Beam",
        "Barre de son Sonos Beam Gen 1",
    )

    assert decision.classification is RelevanceClass.WRONG_VARIANT


def test_compact_headphone_model_matches_hyphenated_target() -> None:
    decision = classify_listing_relevance(
        "Sony WH-1000XM5",
        "Sony WH-1000XM5",
        "Casque Sony WH1000XM5 noir",
    )

    assert decision.classification is RelevanceClass.EXACT_DEVICE


@pytest.mark.parametrize(
    ("target", "query", "listing", "expected"),
    [
        ("Meta Quest 3", "Meta Quest 3", "Meta Quest 3S 128GB", RelevanceClass.WRONG_VARIANT),
        (
            "Apple AirPods Pro 2",
            "AirPods Pro 2",
            "AirPods Pro dernière génération",
            RelevanceClass.UNCERTAIN,
        ),
        ("Nvidia RTX 4070", "RTX 4070", "Nvidia RTX 4070 Ti", RelevanceClass.WRONG_VARIANT),
        ("Dyson Airwrap", "Dyson Airwrap", "Embout seul Dyson Airwrap", RelevanceClass.ACCESSORY),
    ],
)
def test_competing_model_suffixes_are_not_exact_matches(
    target: str, query: str, listing: str, expected: RelevanceClass
) -> None:
    assert classify_listing_relevance(target, query, listing).classification is expected


@pytest.mark.parametrize(
    ("target", "query", "listing"),
    [
        ("Apple iPhone 14", "iPhone 14", "iPhone 14 boîte vide"),
        ("Sony WH-1000XM5", "Sony WH-1000XM5", "Sony WH-1000XM5 carton vide"),
    ],
)
def test_empty_packaging_is_an_accessory(target: str, query: str, listing: str) -> None:
    assert (
        classify_listing_relevance(target, query, listing).classification
        is RelevanceClass.ACCESSORY
    )

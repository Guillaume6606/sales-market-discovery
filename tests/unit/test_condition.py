import pytest

from libs.common.condition import normalize_condition


@pytest.mark.parametrize(
    "raw, expected",
    [
        # French
        ("Neuf", "new"),
        ("neuf avec étiquette", "new"),
        ("neuf sans étiquette", "new"),
        ("Très bon état", "like_new"),
        ("très bon état", "like_new"),
        ("comme neuf", "like_new"),
        ("Bon état", "good"),
        ("bon état", "good"),
        ("Satisfaisant", "fair"),
        ("État satisfaisant", "fair"),
        # English
        ("Brand New", "new"),
        ("new", "new"),
        ("NIB", "new"),
        ("Like New", "like_new"),
        ("Excellent", "like_new"),
        ("Mint", "like_new"),
        ("Very Good", "good"),
        ("Good", "good"),
        ("Acceptable", "fair"),
        ("Fair", "fair"),
        ("Poor", "fair"),
        # Edge cases
        ("", None),
        (None, None),
        ("unknown garbage", None),
        # Accented chars
        ("très bon état", "like_new"),
        ("Neuf avec étiquette", "new"),
    ],
)
def test_normalize_condition(raw: str | None, expected: str | None) -> None:
    assert normalize_condition(raw) == expected

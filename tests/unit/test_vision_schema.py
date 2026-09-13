import pytest
from pydantic import ValidationError

from libs.common.vision_schema import VisionExtraction, VisionExtractionV2, VisionResult


def payload():
    return dict(
        item_class="accessory",
        model=None,
        variant=None,
        included_accessories=[],
        seller_reported_faults=[],
        visible_damage=None,
        text_photo_conflict=None,
    )


def test_seven_required_keys_and_null_semantics():
    assert VisionExtraction.model_validate(payload()).model_dump() == payload()
    for key in payload():
        missing = payload()
        del missing[key]
        with pytest.raises(ValidationError):
            VisionExtraction.model_validate(missing)
    for extra in ("evidence", "unknown_fields"):
        with pytest.raises(ValidationError):
            VisionExtraction.model_validate({**payload(), extra: []})


@pytest.mark.parametrize(
    "field,value",
    [
        ("item_class", "authentic"),
        ("model", ""),
        ("variant", "x" * 61),
        ("included_accessories", ["x"] * 7),
        ("seller_reported_faults", ["x" * 61]),
        ("visible_damage", [""]),
        ("text_photo_conflict", "false"),
    ],
)
def test_reject_invalid_fields(field, value):
    with pytest.raises(ValidationError):
        VisionExtraction.model_validate({**payload(), field: value})


def test_reject_invalid_json_and_unavailable_visual_claims():
    with pytest.raises(ValidationError):
        VisionExtraction.model_validate_json("not json")
    for update in ({"visible_damage": []}, {"text_photo_conflict": False}):
        with pytest.raises(ValidationError):
            VisionExtraction.model_validate({**payload(), **update}, context={"image_count": 0})


def test_historical_v2_retains_strict_parsing():
    old = dict(
        item_class="accessory",
        model=None,
        variant=None,
        included_accessories=[],
        seller_condition_claims=[],
        visible_defects=[],
        contradictions=[],
        unknown_fields=["model"],
        evidence=[],
    )
    result = VisionResult.model_validate(
        dict(status="completed", schema_version="listing-vision-v2", extraction=old)
    )
    assert isinstance(result.extraction, VisionExtractionV2)

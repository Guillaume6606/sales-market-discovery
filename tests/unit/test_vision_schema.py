import pytest
from pydantic import ValidationError

from libs.common.vision_schema import VisionExtraction


def payload():
    return dict(
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


def test_reject_invalid_class_and_json():
    with pytest.raises(ValidationError):
        VisionExtraction.model_validate({**payload(), "item_class": "authentic"})
    with pytest.raises(ValidationError):
        VisionExtraction.model_validate_json("not json")


def test_reject_absent_image_and_invented_quote():
    for evidence in [
        {"field": "model", "image_index": 0},
        {"field": "model", "text_quote": "invented"},
    ]:
        with pytest.raises(ValidationError):
            VisionExtraction.model_validate(
                {**payload(), "evidence": [evidence]}, context={"image_count": 0, "text": "real"}
            )


def test_text_only_cannot_invent_visible_defects():
    with pytest.raises(ValidationError):
        VisionExtraction.model_validate({**payload(), "visible_defects": ["crack"]})

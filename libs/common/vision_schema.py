"""Factual listing extraction: seller claims remain separate from visible evidence."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

SCHEMA_VERSION = "listing-vision-v3"


class Evidence(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "anyOf": [
                {
                    "properties": {
                        "image_index": {"type": "integer"},
                        "text_quote": {"type": "null"},
                    },
                    "required": ["image_index", "text_quote"],
                },
                {
                    "properties": {
                        "image_index": {"type": "null"},
                        "text_quote": {"type": "string"},
                    },
                    "required": ["image_index", "text_quote"],
                },
            ]
        },
    )
    field: str
    image_index: int | None = Field(default=None, ge=0)
    text_quote: str | None = None

    @model_validator(mode="after")
    def validate_source(self, info: ValidationInfo) -> "Evidence":
        if (self.image_index is None) == (self.text_quote is None):
            raise ValueError("Evidence requires exactly one image or text source")
        context = info.context or {}
        if self.image_index is not None and self.image_index >= context.get("image_count", 3):
            raise ValueError("Image reference out of range")
        if self.text_quote is not None and (
            not self.text_quote or self.text_quote not in context.get("text", self.text_quote)
        ):
            raise ValueError("Evidence quote is absent from listing text")
        return self


class VisionExtractionV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_class: Literal[
        "exact_device", "device_bundle", "accessory", "parts_broken", "wrong_variant", "uncertain"
    ]
    model: str | None
    variant: str | None
    included_accessories: list[str]
    seller_condition_claims: list[str]
    visible_defects: list[str]
    contradictions: list[str]
    unknown_fields: list[str]
    evidence: list[Evidence]

    @model_validator(mode="after")
    def require_visual_evidence(self, info: ValidationInfo) -> "VisionExtractionV2":
        if self.item_class in ("exact_device", "device_bundle", "wrong_variant") and (
            not self.model or not any(e.field in ("model", "item_class") for e in self.evidence)
        ):
            raise ValueError("Device identification requires model and supporting evidence")
        if self.visible_defects and not any(
            e.image_index is not None and e.field == "visible_defects" for e in self.evidence
        ):
            raise ValueError("Visible defects require image evidence")
        return self


ShortPhrase = Annotated[str, Field(min_length=1, max_length=60)]


class VisionExtractionV3(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_class: Literal[
        "exact_device", "device_bundle", "accessory", "parts_broken", "wrong_variant", "uncertain"
    ]
    model: Annotated[str, Field(min_length=1, max_length=100)] | None
    variant: ShortPhrase | None
    included_accessories: list[ShortPhrase] = Field(max_length=6)
    seller_reported_faults: list[ShortPhrase] | None = Field(max_length=6)
    visible_damage: list[ShortPhrase] | None = Field(max_length=6)
    text_photo_conflict: bool | None

    @model_validator(mode="after")
    def reject_unavailable_visual_claims(self, info: ValidationInfo) -> "VisionExtractionV3":
        if (info.context or {}).get("image_count") == 0 and (
            self.visible_damage is not None or self.text_photo_conflict is not None
        ):
            raise ValueError("Absent photos require null damage and conflict")
        return self


VisionExtraction = VisionExtractionV3


class VisionResult(BaseModel):
    status: Literal["disabled", "completed", "text_only", "pending", "budget_exhausted", "error"]
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str = SCHEMA_VERSION
    request_key: str | None = None
    extraction: VisionExtractionV3 | VisionExtractionV2 | None = None
    usage: dict = Field(default_factory=dict)
    cache_hit: bool = False
    error: str | None = None

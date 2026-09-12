"""Factual listing extraction: seller claims remain separate from visible evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

SCHEMA_VERSION = "listing-vision-v1"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
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


class VisionExtraction(BaseModel):
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
    def require_visual_evidence(self, info: ValidationInfo) -> "VisionExtraction":
        if self.item_class in ("exact_device", "device_bundle", "wrong_variant") and (
            not self.model or not any(e.field in ("model", "item_class") for e in self.evidence)
        ):
            raise ValueError("Device identification requires model and supporting evidence")
        if self.visible_defects and not any(
            e.image_index is not None and e.field == "visible_defects" for e in self.evidence
        ):
            raise ValueError("Visible defects require image evidence")
        return self


class VisionResult(BaseModel):
    status: Literal["disabled", "completed", "text_only", "pending", "budget_exhausted", "error"]
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str = SCHEMA_VERSION
    request_key: str | None = None
    extraction: VisionExtraction | None = None
    usage: dict = Field(default_factory=dict)
    cache_hit: bool = False
    error: str | None = None

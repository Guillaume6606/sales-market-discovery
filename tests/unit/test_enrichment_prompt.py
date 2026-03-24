"""Tests for LLM enrichment prompt building and response parsing."""

import json

from ingestion.enrichment_prompt import (
    build_enrichment_prompt,
    parse_enrichment_response,
)


class TestBuildPrompt:
    def test_prompt_includes_listing_data(self):
        prompt = build_enrichment_prompt(
            title="iPhone 15 Pro 256GB",
            description="Selling my iPhone, cause déménagement.",
            condition_raw="Très bon état",
            price=650.0,
            currency="EUR",
            category="electronics",
            brand="Apple",
            pmn=800.0,
            photo_urls=["https://example.com/photo1.jpg"],
            days_since_posted=12,
        )
        assert "iPhone 15 Pro 256GB" in prompt
        assert "déménagement" in prompt

    def test_prompt_includes_category_accessories(self):
        prompt = build_enrichment_prompt(
            title="iPhone 15",
            description="Complete set",
            condition_raw="new",
            price=700.0,
            currency="EUR",
            category="electronics",
            brand="Apple",
            pmn=800.0,
            photo_urls=[],
            days_since_posted=1,
        )
        assert "charger" in prompt.lower() or "cable" in prompt.lower()

    def test_prompt_handles_no_pmn(self):
        prompt = build_enrichment_prompt(
            title="Test",
            description="For sale",
            condition_raw="good",
            price=100.0,
            currency="EUR",
            category="other",
            brand=None,
            pmn=None,
            photo_urls=[],
            days_since_posted=5,
        )
        assert "not available" in prompt.lower()


class TestParseResponse:
    def test_parse_valid_response(self):
        raw = json.dumps(
            {
                "urgency_score": 0.85,
                "urgency_keywords": ["déménagement"],
                "has_original_box": True,
                "has_receipt_or_invoice": False,
                "accessories_included": ["charger", "cable"],
                "accessories_completeness": 0.67,
                "photo_quality_score": 0.4,
                "listing_quality_score": 0.45,
                "condition_confidence": 0.8,
                "fakeness_probability": 0.1,
                "seller_motivation_score": 0.75,
            }
        )
        result = parse_enrichment_response(raw)
        assert result is not None
        assert result["urgency_score"] == 0.85
        assert result["has_original_box"] is True

    def test_parse_clamps_scores(self):
        raw = json.dumps(
            {
                "urgency_score": 1.5,
                "urgency_keywords": [],
                "has_original_box": False,
                "has_receipt_or_invoice": False,
                "accessories_included": [],
                "accessories_completeness": -0.1,
                "photo_quality_score": 0.5,
                "listing_quality_score": 0.5,
                "condition_confidence": 0.5,
                "fakeness_probability": 2.0,
                "seller_motivation_score": 0.5,
            }
        )
        result = parse_enrichment_response(raw)
        assert result["urgency_score"] == 1.0
        assert result["accessories_completeness"] == 0.0
        assert result["fakeness_probability"] == 1.0

    def test_parse_invalid_json(self):
        assert parse_enrichment_response("not json {{{") is None

    def test_parse_missing_keys_returns_none(self):
        assert parse_enrichment_response(json.dumps({"urgency_score": 0.5})) is None

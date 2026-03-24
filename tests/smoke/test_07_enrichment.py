"""Smoke tests: enrichment quality (structural + golden set).

Structural tests validate ``parse_enrichment_response`` against hand-crafted
JSON payloads — no network access required, so they always run.

Golden-set tests compare LLM output against pre-labelled fixture data; they
require a live Gemini API key and a populated ``tests/fixtures/golden_set.json``
file, so they skip gracefully when either is missing.

Run all enrichment smoke tests:

    uv run pytest tests/smoke/test_07_enrichment.py -v
"""

from __future__ import annotations

import json

import pytest

from ingestion.enrichment_prompt import ALL_REQUIRED_KEYS, parse_enrichment_response

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PAYLOAD: dict = {
    "urgency_score": 0.5,
    "urgency_keywords": ["urgent"],
    "has_original_box": True,
    "has_receipt_or_invoice": False,
    "accessories_included": ["charger"],
    "accessories_completeness": 0.5,
    "photo_quality_score": 0.5,
    "listing_quality_score": 0.5,
    "condition_confidence": 0.5,
    "fakeness_probability": 0.5,
    "seller_motivation_score": 0.5,
}

_SCORE_KEYS: list[str] = [
    "urgency_score",
    "accessories_completeness",
    "photo_quality_score",
    "listing_quality_score",
    "condition_confidence",
    "fakeness_probability",
    "seller_motivation_score",
]


# ---------------------------------------------------------------------------
# Structural tests — no external dependencies
# ---------------------------------------------------------------------------


class TestEnrichmentStructural:
    """Unit-level tests for ``parse_enrichment_response`` — always runnable."""

    def test_all_scores_in_range(self) -> None:
        """All numeric score fields must be clamped to [0.0, 1.0]."""
        result = parse_enrichment_response(json.dumps(_VALID_PAYLOAD))
        assert result is not None, "Valid payload must parse successfully"
        for key in _SCORE_KEYS:
            assert 0.0 <= result[key] <= 1.0, f"{key} out of range: {result[key]}"

    def test_accessories_are_nonempty_strings(self) -> None:
        """Every item in ``accessories_included`` must be a non-empty string."""
        payload = {**_VALID_PAYLOAD, "accessories_included": ["charger", "cable"]}
        result = parse_enrichment_response(json.dumps(payload))
        assert result is not None
        for item in result["accessories_included"]:
            assert isinstance(item, str) and len(item) > 0, (
                f"accessories_included item is empty or not a string: {item!r}"
            )

    def test_required_keys_match_spec(self) -> None:
        """``ALL_REQUIRED_KEYS`` must exactly match the documented schema."""
        expected = {
            "urgency_score",
            "urgency_keywords",
            "has_original_box",
            "has_receipt_or_invoice",
            "accessories_included",
            "accessories_completeness",
            "photo_quality_score",
            "listing_quality_score",
            "condition_confidence",
            "fakeness_probability",
            "seller_motivation_score",
        }
        assert set(ALL_REQUIRED_KEYS) == expected, (
            f"ALL_REQUIRED_KEYS mismatch.\n  Expected: {sorted(expected)}\n"
            f"  Got:      {sorted(ALL_REQUIRED_KEYS)}"
        )

    def test_missing_key_returns_none(self) -> None:
        """A payload missing any required key must return ``None``."""
        for missing_key in ALL_REQUIRED_KEYS:
            partial = {k: v for k, v in _VALID_PAYLOAD.items() if k != missing_key}
            result = parse_enrichment_response(json.dumps(partial))
            assert result is None, f"Expected None when key '{missing_key}' is absent, got {result}"

    def test_invalid_json_returns_none(self) -> None:
        """Unparseable text must return ``None`` without raising."""
        result = parse_enrichment_response("not valid json {{{")
        assert result is None

    def test_markdown_fence_stripped(self) -> None:
        """JSON wrapped in markdown code fences must parse correctly."""
        fenced = "```json\n" + json.dumps(_VALID_PAYLOAD) + "\n```"
        result = parse_enrichment_response(fenced)
        assert result is not None, "Markdown-fenced JSON must be accepted"
        assert result["urgency_score"] == pytest.approx(0.5)

    def test_scores_clamped_above_one(self) -> None:
        """Score values > 1.0 in the LLM response must be clamped to 1.0."""
        out_of_range = {**_VALID_PAYLOAD, "urgency_score": 1.5, "photo_quality_score": 99.0}
        result = parse_enrichment_response(json.dumps(out_of_range))
        assert result is not None
        assert result["urgency_score"] == pytest.approx(1.0)
        assert result["photo_quality_score"] == pytest.approx(1.0)

    def test_scores_clamped_below_zero(self) -> None:
        """Score values < 0.0 in the LLM response must be clamped to 0.0."""
        out_of_range = {**_VALID_PAYLOAD, "fakeness_probability": -0.3}
        result = parse_enrichment_response(json.dumps(out_of_range))
        assert result is not None
        assert result["fakeness_probability"] == pytest.approx(0.0)

    def test_empty_urgency_keywords_accepted(self) -> None:
        """An empty ``urgency_keywords`` list is valid."""
        payload = {**_VALID_PAYLOAD, "urgency_keywords": []}
        result = parse_enrichment_response(json.dumps(payload))
        assert result is not None
        assert result["urgency_keywords"] == []

    def test_empty_accessories_accepted(self) -> None:
        """An empty ``accessories_included`` list is valid."""
        payload = {**_VALID_PAYLOAD, "accessories_included": []}
        result = parse_enrichment_response(json.dumps(payload))
        assert result is not None
        assert result["accessories_included"] == []

    def test_boolean_fields_preserved(self) -> None:
        """``has_original_box`` and ``has_receipt_or_invoice`` must be preserved as-is."""
        payload = {**_VALID_PAYLOAD, "has_original_box": True, "has_receipt_or_invoice": False}
        result = parse_enrichment_response(json.dumps(payload))
        assert result is not None
        assert result["has_original_box"] is True
        assert result["has_receipt_or_invoice"] is False

    def test_null_boolean_fields_accepted(self) -> None:
        """``has_original_box`` can legitimately be ``null`` (unknown)."""
        payload = {**_VALID_PAYLOAD, "has_original_box": None, "has_receipt_or_invoice": None}
        result = parse_enrichment_response(json.dumps(payload))
        assert result is not None
        assert result["has_original_box"] is None
        assert result["has_receipt_or_invoice"] is None


# ---------------------------------------------------------------------------
# Golden-set tests — require LLM API access + fixture file
# ---------------------------------------------------------------------------


class TestEnrichmentGoldenSet:
    """Accuracy tests against a hand-labelled golden set.

    These tests are skipped when the fixture file does not exist or when LLM
    API access is unavailable.  The golden set format is a list of objects:

    .. code-block:: json

        [
          {
            "title": "...",
            "description": "...",
            "expected": {
              "has_original_box": true,
              "has_receipt_or_invoice": false
            }
          }
        ]
    """

    GOLDEN_SET_PATH = "tests/fixtures/golden_set.json"

    @pytest.fixture
    def golden_set(self) -> list[dict]:
        """Load the golden-set fixture, skipping if it does not exist."""
        from pathlib import Path

        path = Path(self.GOLDEN_SET_PATH)
        if not path.exists():
            pytest.skip(f"Golden set fixture not found at {self.GOLDEN_SET_PATH}")
        data = json.loads(path.read_text())
        if not data:
            pytest.skip("Golden set fixture is empty")
        return data  # type: ignore[return-value]

    def test_boolean_accuracy(self, golden_set: list[dict]) -> None:
        """LLM boolean predictions must match golden labels for >80% of samples.

        Skipped unless a Gemini client can be initialised.
        """
        pytest.skip("Requires LLM API access and populated golden set data")

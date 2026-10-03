"""Tests for connector data quality audit logic."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ingestion.audit import (
    AuditCapture,
    _build_extracted_fields,
    _get_domain,
    _should_cool_down,
    compute_accuracy,
    compute_connector_accuracy,
    detect_antibot,
    judge_listing,
    parse_llm_verdict,
)
from libs.common.models import ConnectorAudit, ListingObservation


def test_zero_values_are_preserved_for_audit() -> None:
    fields = _build_extracted_fields(
        ListingObservation(
            price=Decimal("0"), seller_rating=Decimal("0"), shipping_cost=Decimal("0")
        )
    )
    assert fields["price"] == 0.0
    assert fields["seller_rating"] == 0.0
    assert fields["shipping_cost"] == 0.0


@pytest.mark.asyncio
async def test_leboncoin_condition_is_submitted_for_verification(monkeypatch) -> None:
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(
        text='{"fields": {"condition": {"verdict": "correct"}}}', usage_metadata=None
    )
    monkeypatch.setattr("libs.common.llm_service.get_genai_client", lambda: client)
    result = await judge_listing(
        ListingObservation(obs_id=1, source="leboncoin", condition="Bon état"),
        AuditCapture(screenshot_path=None, html_snippet="<p>Bon état</p>"),
    )
    prompt = client.models.generate_content.call_args.kwargs["contents"][-1]
    assert "known API limitations" not in prompt
    assert result["field_results"]["condition"]["verdict"] == "correct"


def test_audit_accuracy_reports_eligible_sample_and_unverifiable_counts() -> None:
    records = [
        ConnectorAudit(
            source="vinted",
            accuracy_score=Decimal("0.5"),
            field_results={"price": {"verdict": "correct"}, "condition": {"verdict": "incorrect"}},
        ),
        ConnectorAudit(
            source="vinted",
            accuracy_score=None,
            field_results={"price": {"verdict": "unverifiable"}},
        ),
    ]
    summary = compute_connector_accuracy(records)["vinted"]
    assert summary["accuracy"] == 0.5
    assert summary["sample_size"] == 1
    assert summary["total_audits"] == 2
    assert summary["unverifiable_audits"] == 1
    assert summary["field_counts"]["price"] == {"correct": 1, "incorrect": 0, "unverifiable": 1}


def test_all_unverifiable_audits_have_no_accuracy_denominator() -> None:
    summary = compute_connector_accuracy(
        [
            ConnectorAudit(
                source="vinted",
                accuracy_score=None,
                field_results={"price": {"verdict": "unverifiable"}},
            )
        ]
    )["vinted"]
    assert summary["sample_size"] == 0
    assert summary["unverifiable_audits"] == 1
    assert summary["accuracy"] is None
    assert summary["status"] == "unknown"


class TestParseVerdict:
    def test_valid_json_response(self):
        raw = {
            "fields": {
                "price": {"verdict": "correct"},
                "title": {"verdict": "correct"},
                "condition": {"verdict": "incorrect", "expected": "Bon état", "extracted": None},
            },
            "overall": "partial_match",
            "notes": "Condition missing",
        }
        result = parse_llm_verdict(raw)
        assert result["price"]["verdict"] == "correct"
        assert result["condition"]["verdict"] == "incorrect"
        assert len(result) == 3

    def test_missing_fields_key(self):
        raw = {"notes": "malformed"}
        result = parse_llm_verdict(raw)
        assert result == {}

    def test_invalid_verdict_value_treated_as_unverifiable(self):
        raw = {"fields": {"price": {"verdict": "maybe"}}}
        result = parse_llm_verdict(raw)
        assert result["price"]["verdict"] == "unverifiable"


class TestComputeAccuracy:
    def test_all_correct(self):
        fields = {
            "price": {"verdict": "correct"},
            "title": {"verdict": "correct"},
            "condition": {"verdict": "correct"},
        }
        assert compute_accuracy(fields) == 1.0

    def test_one_incorrect(self):
        fields = {
            "price": {"verdict": "correct"},
            "title": {"verdict": "incorrect"},
        }
        assert compute_accuracy(fields) == 0.5

    def test_unverifiable_excluded(self):
        fields = {
            "price": {"verdict": "correct"},
            "title": {"verdict": "correct"},
            "shipping_cost": {"verdict": "unverifiable"},
        }
        assert compute_accuracy(fields) == 1.0

    def test_all_unverifiable_returns_none(self):
        fields = {"price": {"verdict": "unverifiable"}}
        assert compute_accuracy(fields) is None

    def test_empty_fields(self):
        assert compute_accuracy({}) is None


class TestDetectAntibot:
    def test_normal_page_antibot_script_is_not_a_block(self) -> None:
        html = """<html><head><script src="https://js.datadome.co/tags.js"></script>
        <script>const blocked = false; const captcha = null;</script></head>
        <body><h1>iPhone 14 Pro</h1><span>450 €</span></body></html>"""
        assert detect_antibot(html) is False

    def test_captcha_detected(self):
        html = "<html><body><div class='captcha'>Please verify you are human</div></body></html>"
        assert detect_antibot(html) is True

    def test_login_wall_detected(self):
        html = "<html><body><form>Connectez-vous pour continuer</form></body></html>"
        assert detect_antibot(html) is True

    def test_normal_page(self):
        html = "<html><body><h1>iPhone 14 Pro</h1><span>85 €</span></body></html>"
        assert detect_antibot(html) is False

    def test_robot_check(self):
        html = "<html><body>Are you a robot?</body></html>"
        assert detect_antibot(html) is True

    def test_empty_html(self):
        assert detect_antibot("") is False

    def test_datadome_detected(self) -> None:
        html = "<html><head><title>DataDome</title></head></html>"
        assert detect_antibot(html) is True

    def test_captcha_delivery_detected(self) -> None:
        html = '<script src="https://geo.captcha-delivery.com/c.js"></script>'
        assert detect_antibot(html) is True


class TestDomainCooling:
    def test_get_domain_vinted(self) -> None:
        assert _get_domain("https://www.vinted.fr/items/123") == "www.vinted.fr"

    def test_get_domain_leboncoin(self) -> None:
        assert _get_domain("https://www.leboncoin.fr/ad/456") == "www.leboncoin.fr"

    def test_get_domain_empty(self) -> None:
        assert _get_domain("") == ""

    def test_should_cool_at_threshold(self) -> None:
        assert _should_cool_down(5, 5) is True

    def test_should_not_cool_below_threshold(self) -> None:
        assert _should_cool_down(4, 5) is False

    def test_should_cool_above_threshold(self) -> None:
        assert _should_cool_down(7, 5) is True

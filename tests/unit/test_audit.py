"""Tests for connector data quality audit logic."""

from ingestion.audit import (
    compute_accuracy,
    detect_antibot,
    parse_llm_verdict,
)


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

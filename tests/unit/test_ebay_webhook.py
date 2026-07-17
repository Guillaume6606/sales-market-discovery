"""Tests for the eBay account deletion compliance webhook."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from backend.routers.ebay_webhook import compute_challenge_response
from libs.common.settings import settings

TOKEN = "a" * 40
ENDPOINT = "https://smd.example.com/webhooks/ebay/account-deletion"


@pytest.fixture()
def client():
    from backend.main import app

    return TestClient(app)


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(settings, "ebay_verification_token", TOKEN)
    monkeypatch.setattr(settings, "ebay_deletion_endpoint_url", ENDPOINT)


class TestChallengeResponse:
    def test_hash_matches_ebay_concatenation_order(self):
        expected = hashlib.sha256(("code123" + TOKEN + ENDPOINT).encode()).hexdigest()
        assert compute_challenge_response("code123", TOKEN, ENDPOINT) == expected

    def test_get_returns_challenge_response(self, client, configured):
        response = client.get(
            "/webhooks/ebay/account-deletion", params={"challenge_code": "code123"}
        )
        assert response.status_code == 200
        assert response.json() == {
            "challengeResponse": compute_challenge_response("code123", TOKEN, ENDPOINT)
        }

    def test_get_without_config_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ebay_verification_token", None)
        monkeypatch.setattr(settings, "ebay_deletion_endpoint_url", None)
        response = client.get(
            "/webhooks/ebay/account-deletion", params={"challenge_code": "code123"}
        )
        assert response.status_code == 503

    def test_get_without_challenge_code_returns_422(self, client, configured):
        response = client.get("/webhooks/ebay/account-deletion")
        assert response.status_code == 422


class TestDeletionNotification:
    def test_post_notification_acked_with_200(self, client, configured):
        payload = {
            "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
            "notification": {
                "notificationId": "abc-123",
                "data": {"username": "some_user", "userId": "ma8vp1jySJC"},
            },
        }
        response = client.post("/webhooks/ebay/account-deletion", json=payload)
        assert response.status_code == 200

    def test_post_malformed_body_still_acked(self, client, configured):
        response = client.post(
            "/webhooks/ebay/account-deletion",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200

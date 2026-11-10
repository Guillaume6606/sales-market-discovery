"""Alert event listing contracts against real PostgreSQL."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from libs.common.models import AlertEvent, AlertRule, ListingObservation


@pytest.fixture()
def alert_event(
    integration_db: Session, scored_listings: list[ListingObservation], seed_product: str
) -> AlertEvent:
    rule = AlertRule(name="Bargain", threshold_pct=-10, is_active=True)
    integration_db.add(rule)
    integration_db.flush()
    event = AlertEvent(
        rule_id=rule.rule_id,
        product_id=seed_product,
        obs_id=scored_listings[0].obs_id,
        idempotency_key=f"{rule.rule_id}:{scored_listings[0].obs_id}",
        delivery_status="failed",
        delivery_attempts=1,
        suppressed=False,
    )
    integration_db.add(event)
    integration_db.commit()
    return event


@pytest.mark.parametrize("params", [{"product_id": "d34710cb"}, {"rule_id": "not-a-rule"}])
def test_alert_event_filters_reject_malformed_ids_without_server_error(
    client: TestClient, alert_event: AlertEvent, params: dict[str, str]
) -> None:
    response = client.get("/alerts/events", params=params)

    assert response.status_code == 422


def test_alert_events_identify_the_alerted_listing(
    client: TestClient, alert_event: AlertEvent, scored_listings: list[ListingObservation]
) -> None:
    response = client.get("/alerts/events", params={"product_id": str(alert_event.product_id)})

    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["listing_title"] == scored_listings[0].title
    assert event["listing_price"] == 500.0
    assert event["listing_url"] == scored_listings[0].url
    assert event["listing_source"] == "leboncoin"


def test_changed_feedback_records_when_it_was_updated(
    client: TestClient, alert_event: AlertEvent
) -> None:
    path = f"/alerts/events/{alert_event.alert_id}/feedback"
    assert client.post(path, json={"feedback": "interested"}).status_code == 201
    assert client.post(path, json={"feedback": "purchased"}).status_code == 201

    feedback = client.get(path).json()["feedback"]

    assert feedback["feedback"] == "purchased"
    assert feedback["updated_at"] > feedback["created_at"]

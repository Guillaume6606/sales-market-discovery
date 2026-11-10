from pathlib import Path
from typing import Any

import httpx
import streamlit as st
from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from ui.lib import api

PAGE = Path(__file__).parents[2] / "ui" / "pages" / "6_Alerts.py"

EVENT = {
    "alert_id": 7,
    "rule_id": "rule-1",
    "product_id": "b06d630a-54d2-4eb6-a42b-6c40d34710cb",
    "obs_id": 1,
    "sent_at": None,
    "created_at": "2026-11-10T19:36:05+00:00",
    "delivery": {},
    "delivery_status": "failed",
    "delivery_attempts": 1,
    "next_attempt_at": None,
    "suppressed": False,
    "feedback": None,
    "listing_title": "Casque Sony WH-1000XM4 noir",
    "listing_price": 140.0,
    "listing_url": "https://www.leboncoin.fr/ad/audio/123456",
    "listing_source": "leboncoin",
}


def _install_fakes(
    monkeypatch: MonkeyPatch, events_response: httpx.Response, precision: dict[str, Any] | None
) -> None:
    st.cache_data.clear()

    def fake_get(path: str, **_kwargs: Any) -> httpx.Response:
        if path == "/alerts/events":
            return events_response
        if path == "/analytics/alert-precision":
            return httpx.Response(200, json=precision, request=events_response.request)
        if path == "/alerts/rules":
            return httpx.Response(
                200,
                json={"rules": [{"rule_id": "rule-1", "name": "Sony bargains"}]},
                request=events_response.request,
            )
        raise AssertionError(f"Unexpected API path: {path}")

    monkeypatch.setattr(api, "api_get", fake_get)


def _events(status_code: int, payload: Any) -> httpx.Response:
    return httpx.Response(
        status_code, json=payload, request=httpx.Request("GET", "http://api/alerts/events")
    )


def test_alert_events_show_which_listing_was_alerted(monkeypatch: MonkeyPatch) -> None:
    _install_fakes(monkeypatch, _events(200, {"events": [EVENT], "total": 1}), None)

    app = AppTest.from_file(PAGE)
    app.run()

    assert not app.exception
    rendered = " ".join(str(element.value) for element in [*app.markdown, *app.caption])
    assert "Casque Sony WH-1000XM4 noir" in rendered
    assert "€140.00" in rendered
    assert "https://www.leboncoin.fr/ad/audio/123456" in rendered


def test_alert_events_load_failure_is_reported_not_shown_as_empty(
    monkeypatch: MonkeyPatch,
) -> None:
    _install_fakes(monkeypatch, _events(422, {"detail": "bad product id"}), None)

    app = AppTest.from_file(PAGE)
    app.run()

    assert any("alert events" in error.value.lower() for error in app.error)
    assert not any(info.value == "No alert events found" for info in app.info)

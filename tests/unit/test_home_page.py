from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from ui.lib import api

PAGE = Path(__file__).parents[2] / "ui" / "pages" / "0_Home.py"


def test_recent_alerts_name_the_listing_and_show_undelivered_alerts(
    monkeypatch: MonkeyPatch,
) -> None:
    events = {
        "events": [
            {
                "alert_id": 1,
                "product_id": "974024a1-0000-0000-0000-000000000000",
                "sent_at": None,
                "created_at": "2020-01-01T10:00:00+00:00",
                "delivery_status": "failed",
                "suppressed": False,
                "listing_title": "Casque Sony WH-1000XM4 noir",
            }
        ],
        "total": 1,
    }
    monkeypatch.setattr(api, "fetch_health_overview", lambda: None)
    monkeypatch.setattr(api, "fetch_analytics", lambda: None)
    monkeypatch.setattr(api, "fetch_ingestion_health", lambda: [])
    monkeypatch.setattr(api, "fetch_alert_events", lambda **_kwargs: events)
    monkeypatch.setattr("streamlit.page_link", lambda *_args, **_kwargs: None)

    app = AppTest.from_file(PAGE)
    app.run()

    assert not app.exception
    lines: list[Any] = [m.value for m in app.markdown if m.value.startswith("- ")]
    assert len(lines) == 1
    assert "Casque Sony WH-1000XM4 noir" in lines[0]
    assert "Never" not in lines[0]
    assert "failed" in lines[0].lower()

from pathlib import Path
from typing import Any

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from ui.lib import api
from ui.lib.config import SUPPORTED_PROVIDERS

PAGE = Path(__file__).parents[2] / "ui" / "pages" / "5_Health.py"


class _Response:
    def raise_for_status(self) -> None:
        raise RuntimeError("unavailable")


def test_health_page_describes_fetch_outcomes_like_the_backend(
    monkeypatch: MonkeyPatch,
) -> None:
    connector: dict[str, Any] = {
        "source": "cashconverters",
        "success_rate_24h": 0.5,
        "success_rate_7d": 0.5,
        "completed_runs_24h": 2,
    }
    monkeypatch.setattr(api, "fetch_health_overview", lambda: None)
    monkeypatch.setattr(api, "fetch_ingestion_health", lambda: [connector])
    monkeypatch.setattr(api, "fetch_product_health", lambda: [])
    monkeypatch.setattr(api, "fetch_ingestion_runs", lambda **_kwargs: {"runs": [], "total": 0})
    monkeypatch.setattr(api, "fetch_computation_status", lambda: None)
    monkeypatch.setattr(api, "fetch_pmn_accuracy_aggregate", lambda: None)
    monkeypatch.setattr(api, "fetch_audit_results", lambda **_kwargs: None)
    monkeypatch.setattr(api, "api_get", lambda *_args, **_kwargs: _Response())

    app = AppTest.from_file(PAGE)
    app.run()

    captions = [caption.value for caption in app.caption]
    assert not any("Successful fetches include empty results" in text for text in captions)
    assert any("Empty results count as no data" in text for text in captions)
    assert app.selectbox(key="health_run_source").options == ["All", *SUPPORTED_PROVIDERS]

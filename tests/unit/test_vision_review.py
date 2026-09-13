"""Human review persistence and blind comparison checks."""

from pathlib import Path

from ui.lib.vision_review import FIELDS, blind_order, load_reviews, save_review


def test_blind_order_is_stable_and_listing_specific() -> None:
    models = ["one", "two", "three", "four", "five"]
    assert blind_order("a", models) == blind_order("a", models)
    assert set(blind_order("a", models)) == set(models)
    assert blind_order("a", models) != blind_order("b", models)


def test_save_preserves_other_listings_and_first_assessment(tmp_path: Path) -> None:
    path = tmp_path / "reviews.json"
    save_review(path, "a", {"reference": {"item_class": "accessory"}})
    save_review(path, "b", {"reference": {"item_class": "exact_device"}})
    save_review(path, "a", {"ratings": {"model": {"visible_damage": "Incorrect"}}})
    rows = load_reviews(path)
    assert rows["a"]["reference"]["item_class"] == "accessory"
    assert rows["b"]["reference"]["item_class"] == "exact_device"
    assert rows["a"]["ratings"]["model"]["visible_damage"] == "Incorrect"
    assert "updated_at" in rows["a"]
    assert len(FIELDS) == 7


def test_review_ui_save_reveal_and_resume(tmp_path: Path, monkeypatch) -> None:
    import json

    from streamlit.testing.v1 import AppTest

    import ui.vision_review as view
    from ui.lib.vision_review import MODELS

    listing = {
        "id": "42",
        "input_hash": "fixed",
        "split": "selection",
        "target": "PS5",
        "title": "Console",
        "description": "Console with cable",
        "source": "test",
        "image_hashes": [],
    }
    (tmp_path / "manifest.jsonl").write_text(json.dumps(listing))
    result = {
        "id": "42",
        "input_hash": "fixed",
        "extraction": dict.fromkeys(FIELDS),
        "status": "completed",
        "wall_ms": 1000,
        "cost": "0.001",
    }
    for filename in MODELS.values():
        (tmp_path / filename).write_text(json.dumps(result))
    monkeypatch.setattr(view, "ROOT", tmp_path)
    monkeypatch.setattr(view, "REVIEWS", tmp_path / "reviews.json")
    app = AppTest.from_string("from ui.vision_review import main\nmain()")
    app.run()
    assert not app.exception
    assert not any("Compare the five" in x.value for x in app.subheader)
    next(b for b in app.button if b.label == "Save assessment & reveal answers").click().run()
    assert not app.exception
    assert any("Compare the five" in x.value for x in app.subheader)
    assert not any(name in x.value for name in MODELS for x in app.caption)
    rating = next(s for s in app.selectbox if s.label == "Judge Item category")
    rating.set_value("Incorrect")
    next(b for b in app.button if b.label == "Save comparison").click().run()
    assert not app.exception
    saved = load_reviews(tmp_path / "reviews.json")
    assert saved["42"]["reference"]["item_class"] == "uncertain"
    assert any(r["item_class"] == "Incorrect" for r in saved["42"]["ratings"].values())
    app.checkbox[0].check().run()
    assert not app.exception
    assert len(app.dataframe) == 2
    app.run()
    assert next(s for s in app.selectbox if s.label == "Judge Item category").value == "Incorrect"

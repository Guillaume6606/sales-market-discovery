from libs.common.llm_service import assess_listing_relevance
from libs.common.models import ProductTemplate


def test_disabled_llm_returns_non_passing_uncertain(monkeypatch, listing_factory) -> None:
    monkeypatch.setattr("libs.common.llm_service.get_genai_client", lambda: None)

    result = assess_listing_relevance(
        listing_factory(),
        None,
        ProductTemplate(name="Sony PlayStation 5", search_query="PlayStation 5"),
        [],
    )

    assert result["is_relevant"] is False
    assert result["classification"] == "uncertain"
    assert result["flags"] == ["validation_unavailable"]


def test_llm_classification_uses_deterministic_decoding(monkeypatch, listing_factory) -> None:
    captured_config = {}

    class Models:
        def generate_content(self, *, model, contents, config):
            captured_config.update(config)
            return type(
                "Response",
                (),
                {
                    "text": (
                        '{"classification":"exact_device","confidence":0.9,'
                        '"reasoning":"model matches","flags":[]}'
                    )
                },
            )()

    client = type("Client", (), {"models": Models()})()
    monkeypatch.setattr("libs.common.llm_service.get_genai_client", lambda: client)

    result = assess_listing_relevance(
        listing_factory(title="Sony PlayStation 5"),
        None,
        ProductTemplate(name="Sony PlayStation 5", search_query="PlayStation 5"),
        [],
    )

    assert result["is_relevant"] is True
    assert captured_config["temperature"] == 0

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.common import telegram_service
from libs.common.telegram_service import (
    TELEGRAM_CAPTION_LIMIT,
    TELEGRAM_TEXT_LIMIT,
    format_opportunity_alert,
    negotiation_draft,
    negotiation_offer_eur,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _opportunity(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "estimated_sale_price_eur": 1150.0,
        "contribution_eur": 120.0,
        "max_buy_price_eur": 905.0,
        "minimum_contribution_eur": 30.0,
        "contribution_at_max_buy_eur": 30.4,
        "offer_price_eur": 765.0,
        "contribution_at_offer_eur": 207.5,
        "reference_id": "0f1e2d3c-aaaa-bbbb-cccc-000000000001",
        "destination_marketplace": "ebay",
        "reference_condition": "good",
    }
    values.update(overrides)
    return values


def _listing(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "listing_id": "lbc-1",
        "source": "leboncoin",
        "title": "Sony FE 24-70mm f/2.8 GM II",
        "price": 850.0,
        "shipping_cost": 12.0,
        "url": "https://www.leboncoin.fr/ad/photo_et_video/123",
        "condition": "Très bon état",
        "seller_rating": None,
        "first_seen_at": NOW - timedelta(hours=3, minutes=10),
    }
    values.update(overrides)
    return values


PRODUCT = {"product_id": "p1", "name": "Sony FE 24-70mm f/2.8 GM II", "brand": "Sony"}


def _render(**kwargs: Any) -> str:
    return format_opportunity_alert(
        kwargs.get("opportunity", _opportunity()),
        kwargs.get("listing", _listing()),
        kwargs.get("product", PRODUCT),
        now=NOW,
    )


class TestNegotiationOffer:
    @pytest.mark.parametrize(
        ("ask", "max_buy", "expected"),
        [
            (850, 905, 765),
            (1000, 880, 880),
            (1000, 883.99, 880),
            (1000, 1200, 900),
            (999, 2000, 895),
        ],
    )
    def test_offer_is_ten_percent_below_ask_capped_by_max_and_rounded_down(
        self, ask: float, max_buy: float, expected: int
    ) -> None:
        assert negotiation_offer_eur(ask, max_buy) == expected

    @pytest.mark.parametrize(("ask", "max_buy"), [(None, 900), (900, None), (900, 3), (0, 900)])
    def test_no_offer_without_a_usable_price(
        self, ask: float | None, max_buy: float | None
    ) -> None:
        assert negotiation_offer_eur(ask, max_buy) is None

    def test_offer_never_exceeds_max_buy_or_ask(self) -> None:
        for ask in range(50, 3000, 37):
            for max_buy in range(10, 3000, 53):
                offer = negotiation_offer_eur(ask, max_buy)
                if offer is not None:
                    assert 0 < offer <= max_buy
                    assert offer < ask

    def test_draft_is_short_polite_french_and_states_the_offer(self) -> None:
        draft = negotiation_draft("Sony FE 24-70mm f/2.8 GM II", 765)
        assert draft.startswith("Bonjour")
        assert "765 € (hors frais de port)" in draft
        assert "toujours disponible" in draft
        assert "numéro de série" in draft
        assert "Merci" in draft
        assert len(draft) <= 400


class TestFormatOpportunityAlert:
    def test_complete_alert_contains_every_decision_field(self) -> None:
        text = _render()
        assert "Sony FE 24-70mm f/2.8 GM II" in text
        assert "Prix demandé : 850,00 €" in text
        assert "port 12,00 €" in text
        assert "862,00 €" in text
        assert "Prix d'achat max (estimé) : 905,00 €" in text
        assert "Revente estimée" in text and "1 150,00 €" in text
        assert "au prix demandé : 120,00 €" in text
        assert "au prix max 905,00 € : 30,40 €" in text
        assert "à l'offre 765,00 € : 207,50 €" in text
        assert "État annoncé : Très bon état" in text
        assert "il y a 3 h" in text
        assert 'href="https://www.leboncoin.fr/ad/photo_et_video/123"' in text
        assert "numéro de série" in text and "disponibilité" in text
        assert "<pre>Bonjour" in text
        assert "765 €" in text

    def test_every_estimated_figure_is_labelled_estimated(self) -> None:
        lines = _render().splitlines()
        assert any(line.startswith("💵 <b>Contribution nette estimée</b>") for line in lines)
        assert any("Prix d'achat max (estimé)" in line for line in lines)
        assert any(line.startswith("📊 Revente estimée") for line in lines)
        bullets = [line for line in lines if line.startswith("  • ")]
        assert len(bullets) == 3
        header = lines.index(next(line for line in lines if "Contribution nette" in line))
        assert lines[header + 1 : header + 4] == bullets

    def test_asking_price_is_never_presented_as_a_sale_price(self) -> None:
        text = _render().casefold()
        assert "prix de vente" not in text
        assert "sale price" not in text
        assert "prix demandé" in text

    def test_unknown_shipping_is_explicit_and_no_total_is_invented(self) -> None:
        text = _render(listing=_listing(shipping_cost=None))
        assert "port inconnu" in text
        assert "862,00" not in text
        assert "frais de port à demander" in text

    def test_missing_fields_render_without_crashing(self) -> None:
        listing = _listing(title=None, condition=None, first_seen_at=None, url=None, price=None)
        opportunity = _opportunity(
            max_buy_price_eur=None,
            contribution_at_max_buy_eur=None,
            offer_price_eur=None,
            contribution_at_offer_eur=None,
            contribution_eur=None,
            estimated_sale_price_eur=None,
            reference_id=None,
            destination_marketplace=None,
        )
        text = _render(listing=listing, opportunity=opportunity, product={})
        assert "État annoncé : non renseigné" in text
        assert "Première observation : inconnue" in text
        assert "lien indisponible" in text.casefold()
        assert "non calculé" in text
        assert "<pre>" not in text

    def test_html_special_characters_are_escaped(self) -> None:
        listing = _listing(
            title="<b>Sony</b> & Co 24-70 GM II",
            condition="<i>neuf</i>",
            url='https://www.leboncoin.fr/ad/1?a="x"&b=<y>',
        )
        text = _render(listing=listing, product={"name": "Sony <GM> & II"})
        assert "<b>Sony</b>" not in text
        assert "&lt;b&gt;Sony&lt;/b&gt; &amp; Co" in text
        assert "&lt;i&gt;neuf&lt;/i&gt;" in text
        assert "Sony &lt;GM&gt; &amp; II" in text
        assert 'href="https://www.leboncoin.fr/ad/1?a=&quot;x&quot;&amp;b=&lt;y&gt;"' in text

    def test_non_http_url_is_not_linked(self) -> None:
        text = _render(listing=_listing(url="javascript:alert(1)"))
        assert "href" not in text
        assert "lien indisponible" in text.casefold()

    def test_message_stays_within_telegram_text_limit_with_huge_fields(self) -> None:
        listing = _listing(title="Sony & <GM> " * 2000, condition="x" * 5000)
        text = _render(listing=listing, product={"name": "N" * 5000})
        assert len(text) <= TELEGRAM_TEXT_LIMIT
        assert "…" in text

    def test_worst_case_html_escaping_stays_within_telegram_text_limit(self) -> None:
        hostile = '&"<>' * 3000
        listing = _listing(title=hostile, condition=hostile, source=hostile)
        opportunity = _opportunity(reference_id=hostile, destination_marketplace=hostile)
        text = format_opportunity_alert(opportunity, listing, {"name": hostile}, now=NOW)
        assert len(text) <= TELEGRAM_TEXT_LIMIT
        assert '"<>' not in text

    @pytest.mark.parametrize(
        ("age", "expected"),
        [
            (timedelta(minutes=12), "il y a 12 min"),
            (timedelta(hours=5), "il y a 5 h"),
            (timedelta(days=3, hours=2), "il y a 3 j"),
        ],
    )
    def test_listing_age_from_first_observation(self, age: timedelta, expected: str) -> None:
        assert expected in _render(listing=_listing(first_seen_at=NOW - age))

    def test_naive_first_seen_is_treated_as_utc(self) -> None:
        naive = (NOW - timedelta(hours=2)).replace(tzinfo=None)
        assert "il y a 2 h" in _render(listing=_listing(first_seen_at=naive))


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    fake.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    fake.send_photo = AsyncMock(return_value=MagicMock(message_id=8))
    monkeypatch.setattr(telegram_service, "_get_bot", lambda: fake)
    monkeypatch.setattr(telegram_service.settings, "telegram_chat_id", "123")
    return fake


class TestSendOpportunityAlert:
    async def test_feedback_buttons_are_unchanged(self, bot: MagicMock) -> None:
        result = await telegram_service.send_opportunity_alert(
            _opportunity(), _listing(), PRODUCT, alert_id=42
        )
        assert result["status"] == "success"
        kwargs = bot.send_message.await_args.kwargs
        assert kwargs["parse_mode"] == "HTML"
        markup = kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        assert callbacks == ["fb:42:interested", "fb:42:not_interested", "fb:42:purchased"]

    async def test_sent_text_is_the_rendered_alert(self, bot: MagicMock) -> None:
        await telegram_service.send_opportunity_alert(_opportunity(), _listing(), PRODUCT)
        sent = bot.send_message.await_args.kwargs["text"]
        assert "Prix d'achat max (estimé)" in sent
        assert "<pre>Bonjour" in sent

    async def test_long_alert_with_screenshot_is_sent_as_text_not_truncated_caption(
        self, bot: MagicMock, tmp_path: Any
    ) -> None:
        screenshot = tmp_path / "shot.png"
        screenshot.write_bytes(b"png")
        await telegram_service.send_opportunity_alert(
            _opportunity(), _listing(), PRODUCT, screenshot_path=str(screenshot), alert_id=1
        )
        assert len(_render()) > TELEGRAM_CAPTION_LIMIT
        bot.send_photo.assert_not_awaited()
        bot.send_message.assert_awaited_once()


def test_decimal_inputs_are_accepted() -> None:
    opportunity = _opportunity(
        estimated_sale_price_eur=Decimal("1150.00"), max_buy_price_eur=Decimal("905.00")
    )
    text = format_opportunity_alert(
        opportunity, _listing(price=Decimal("850.00")), PRODUCT, now=NOW
    )
    assert "905,00 €" in text

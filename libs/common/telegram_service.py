"""
Telegram alert service for sending opportunity notifications.
"""

import html
import os
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from libs.common.models import VALID_FEEDBACK_VALUES
from libs.common.settings import settings

try:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.error import RetryAfter, TelegramError, TimedOut

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not available. Telegram alerts will be disabled.")


def _get_bot() -> Bot | None:
    """Initialize and return Telegram bot instance."""
    if not TELEGRAM_AVAILABLE:
        return None

    if not settings.telegram_bot_token:
        logger.warning("Telegram bot token not configured")
        return None

    try:
        bot = Bot(token=settings.telegram_bot_token)
        return bot
    except Exception as e:
        logger.error(f"Failed to initialize Telegram bot: {e}")
        return None


def _confidence_badge(confidence: float | None) -> str:
    """Return a human-readable confidence badge string for Telegram messages."""
    if confidence is None:
        return ""
    pct = round(confidence * 100)
    if confidence >= 0.7:
        label = "high"
    elif confidence >= 0.4:
        label = "medium"
    else:
        label = "low"
    return f"\n📈 <b>Confidence:</b> {label} ({pct}%)"


TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
NEGOTIATION_DISCOUNT = Decimal("0.90")
NEGOTIATION_STEP_EUR = 5
_FIELD_LIMIT = 120
_MANUAL_CHECKS = (
    "identité exacte + numéro de série (photo)",
    "disponibilité auprès du vendeur",
    "état réel des lentilles (champignon, rayures, buée)",
    "frais de port / remise en main propre",
)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _eur(value: Any) -> str:
    amount = _to_decimal(value)
    if amount is None:
        return "non calculé"
    grouped = f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
    return grouped.replace(",", " ").replace(".", ",") + " €"


def _text(value: Any, fallback: str, limit: int = _FIELD_LIMIT) -> str:
    raw = " ".join(str(value).split()) if value not in (None, "") else ""
    if not raw:
        return fallback
    if len(raw) > limit:
        raw = raw[: limit - 1].rstrip() + "…"
    return html.escape(raw)


def _age(first_seen_at: Any, now: datetime) -> str:
    if not isinstance(first_seen_at, datetime):
        return "inconnue"
    seen = first_seen_at if first_seen_at.tzinfo else first_seen_at.replace(tzinfo=UTC)
    minutes = max(0, int((now - seen).total_seconds() // 60))
    if minutes < 60:
        ago = f"il y a {minutes} min"
    elif minutes < 48 * 60:
        ago = f"il y a {minutes // 60} h"
    else:
        ago = f"il y a {minutes // (24 * 60)} j"
    return f"{ago} ({seen.astimezone(UTC):%d/%m %H:%M} UTC)"


def negotiation_offer_eur(ask: Any, max_buy: Any) -> int | None:
    """Offer 10% below the asking price, capped by the maximum purchase price.

    The result is rounded down to a multiple of 5 EUR, so it never exceeds either bound.
    """
    asking = _to_decimal(ask)
    ceiling = _to_decimal(max_buy)
    if asking is None or ceiling is None or asking <= 0:
        return None
    target = min(asking * NEGOTIATION_DISCOUNT, ceiling)
    offer = int(target // NEGOTIATION_STEP_EUR) * NEGOTIATION_STEP_EUR
    return offer if offer > 0 else None


def negotiation_draft(title: str | None, offer_eur: int) -> str:
    subject = " ".join(str(title or "votre objectif").split())[:60]
    return (
        f"Bonjour, votre annonce « {subject} » m'intéresse. Est-elle toujours disponible ? "
        f"Je vous propose {offer_eur} € (hors frais de port). Pourriez-vous m'envoyer une "
        "photo du numéro de série et des lentilles avant/arrière ? Merci et bonne journée."
    )


def format_opportunity_alert(
    opportunity: dict[str, Any],
    listing: dict[str, Any],
    product_template: dict[str, Any],
    *,
    now: datetime | None = None,
    pmn_confidence: float | None = None,
) -> str:
    """Render a self-contained Telegram HTML alert for a manual purchase decision."""
    now = now or datetime.now(UTC)
    product_name = _text(product_template.get("name"), "Produit inconnu")
    title = _text(listing.get("title"), "titre non renseigné")
    source = _text(listing.get("source"), "source inconnue", 40)
    destination = _text(opportunity.get("destination_marketplace"), "destination à vérifier", 40)
    ask = _to_decimal(listing.get("price"))
    shipping = _to_decimal(listing.get("shipping_cost"))
    max_buy = _to_decimal(opportunity.get("max_buy_price_eur"))
    offer = _to_decimal(opportunity.get("offer_price_eur"))

    if ask is None:
        price_line = "💰 Prix demandé : non renseigné"
    elif shipping is None:
        price_line = (
            f"💰 Prix demandé : {_eur(ask)} + port inconnu (frais de port à demander, "
            "non inclus dans les calculs)"
        )
    else:
        price_line = (
            f"💰 Prix demandé : {_eur(ask)} + port {_eur(shipping)} = {_eur(ask + shipping)}"
        )

    url = str(listing.get("url") or "").strip()
    if urlparse(url).scheme in {"http", "https"} and urlparse(url).netloc:
        link = f'🔗 <a href="{html.escape(url, quote=True)}">Voir l\'annonce</a>'
    else:
        link = "🔗 Lien indisponible"

    reference_condition = _text(opportunity.get("reference_condition"), "non précisé", 20)
    reference_id = _text(opportunity.get("reference_id"), "aucune", 40)
    offer_label = f"à l'offre {_eur(offer)}" if offer is not None else "à l'offre"
    lines = [
        "🎯 <b>Opportunité à vérifier (décision manuelle)</b>",
        f"📦 <b>{product_name}</b>",
        f"📋 Annonce : {title} ({source})",
        price_line,
        f"⚖️ <b>Prix d'achat max (estimé) : {_eur(max_buy)}</b>",
        f"📊 Revente estimée : {_eur(opportunity.get('estimated_sale_price_eur'))} "
        f"sur {destination} (référence vérifiée, pas un prix garanti)",
        "💵 <b>Contribution nette estimée</b> (frais, charges, envoi, risque déduits) :",
        f"  • au prix demandé : {_eur(opportunity.get('contribution_eur'))}",
        f"  • au prix max {_eur(max_buy)} : {_eur(opportunity.get('contribution_at_max_buy_eur'))}",
        f"  • {offer_label} : {_eur(opportunity.get('contribution_at_offer_eur'))}",
        f"🔎 État annoncé : {_text(listing.get('condition'), 'non renseigné', 60)} "
        f"(référence : {reference_condition})",
        f"🕒 Première observation : {_age(listing.get('first_seen_at'), now)}",
        link,
        "✅ <b>À vérifier à la main</b> : " + " ; ".join(_MANUAL_CHECKS),
    ]
    if offer is not None and max_buy is not None:
        draft = negotiation_draft(listing.get("title"), int(offer))
        lines.append(f"✉️ <b>Brouillon d'offre</b> ({_eur(offer)} ≤ max {_eur(max_buy)}) :")
        lines.append(f"<pre>{html.escape(draft, quote=False)}</pre>")
    lines.append(f"🧾 Référence de valorisation : {reference_id}")
    badge = _confidence_badge(pmn_confidence)
    if badge:
        lines.append(badge.strip())
    return "\n".join(lines)


async def send_opportunity_alert(
    opportunity: dict[str, Any],
    listing: dict[str, Any],
    product_template: dict[str, Any],
    screenshot_path: str | None = None,
    pmn_confidence: float | None = None,
    alert_id: int | None = None,
) -> dict[str, Any]:
    """
    Send opportunity alert via Telegram.

    Args:
        opportunity: Dict with opportunity details (margin, pmn, etc.)
        listing: Dict with listing details (title, price, url, etc.)
        product_template: Dict with product template details
        screenshot_path: Optional path to screenshot image

    Returns:
        Dict with send status and message_id if successful
    """
    bot = _get_bot()
    if not bot:
        return {
            "status": "error",
            "error": "Telegram bot not available",
        }

    if not settings.telegram_chat_id:
        return {
            "status": "error",
            "error": "Telegram chat ID not configured",
        }

    try:
        message_text = format_opportunity_alert(
            opportunity, listing, product_template, pmn_confidence=pmn_confidence
        )

        # Build inline keyboard if alert_id is provided
        reply_markup = None
        if alert_id is not None and TELEGRAM_AVAILABLE:
            # Button labels keyed by feedback value from the canonical constant
            _labels = {
                "interested": "Interested",
                "not_interested": "Not Interested",
                "purchased": "Purchased",
            }
            buttons = [
                InlineKeyboardButton(
                    _labels.get(val, val),
                    callback_data=f"fb:{alert_id}:{val}",
                )
                for val in VALID_FEEDBACK_VALUES
            ]
            reply_markup = InlineKeyboardMarkup([buttons[:2], buttons[2:]])

        # Send message
        try:
            if (
                screenshot_path
                and os.path.exists(screenshot_path)
                and len(message_text) <= TELEGRAM_CAPTION_LIMIT
            ):
                # Send with photo
                with open(screenshot_path, "rb") as photo:
                    message = await bot.send_photo(
                        chat_id=settings.telegram_chat_id,
                        photo=photo,
                        caption=message_text,
                        parse_mode="HTML",
                        disable_web_page_preview=False,
                        reply_markup=reply_markup,
                    )
            else:
                # Send text only
                message = await bot.send_message(
                    chat_id=settings.telegram_chat_id,
                    text=message_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                    reply_markup=reply_markup,
                )

            logger.info(f"Sent Telegram alert for listing {listing.get('listing_id', 'unknown')}")
            return {
                "status": "success",
                "message_id": message.message_id,
                "chat_id": settings.telegram_chat_id,
            }

        except RetryAfter as e:
            logger.warning(f"Telegram rate limit: {e}")
            return {
                "status": "error",
                "error": f"Rate limited: {e.retry_after} seconds",
            }
        except TimedOut:
            logger.warning("Telegram request timed out")
            return {
                "status": "error",
                "error": "Request timed out",
            }
        except TelegramError as e:
            logger.error(f"Telegram API error: {e}")
            return {
                "status": "error",
                "error": str(e),
            }

    except Exception as e:
        logger.error(f"Error sending Telegram alert: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
        }


async def send_system_alert(
    title: str,
    stale_products: list[dict[str, Any]],
    failing_connectors: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Send system health alert via Telegram.

    Args:
        title: Alert title
        stale_products: List of dicts with 'name' and 'hours_since_ingestion'
        failing_connectors: List of dicts with 'name', 'consecutive_failures', 'last_error'

    Returns:
        Dict with send status
    """
    bot = _get_bot()
    if not bot:
        return {"status": "error", "error": "Telegram bot not available"}

    if not settings.telegram_chat_id:
        return {"status": "error", "error": "Telegram chat ID not configured"}

    try:
        sections = []

        if stale_products:
            lines = ["<b>Stale Products:</b>"]
            for p in stale_products:
                name = html.escape(p.get("name", "Unknown"))
                hours = p.get("hours_since_ingestion")
                lines.append(
                    f"  - {name} ({hours:.0f}h since last ingestion)"
                    if hours
                    else f"  - {name} (never ingested)"
                )
            sections.append("\n".join(lines))

        if failing_connectors:
            lines = ["<b>Failing Connectors:</b>"]
            for c in failing_connectors:
                name = html.escape(c.get("name", "Unknown"))
                failures = c.get("consecutive_failures", 0)
                last_error = html.escape(c.get("last_error", "unknown")[:100])
                lines.append(f"  - {name}: {failures} consecutive failures (last: {last_error})")
            sections.append("\n".join(lines))

        message_text = f"\u26a0\ufe0f <b>{html.escape(title)}</b>\n\n" + "\n\n".join(sections)

        message = await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=message_text,
            parse_mode="HTML",
        )

        logger.info(f"Sent system alert: {title}")
        return {"status": "success", "message_id": message.message_id}

    except Exception as e:
        logger.error(f"Error sending system alert: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def send_connector_quality_alert(
    source: str,
    accuracy_data: dict[str, Any],
) -> dict[str, Any]:
    """Send Telegram alert when connector accuracy drops below threshold."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured, skipping quality alert")
        return {"status": "not_configured"}

    accuracy = accuracy_data.get("accuracy")
    per_field = accuracy_data.get("per_field", {})
    sample_size = accuracy_data.get("sample_size", 0)
    threshold = settings.audit_accuracy_yellow

    field_lines = []
    for field, acc in sorted(per_field.items(), key=lambda x: x[1] if x[1] is not None else 0):
        if acc is None:
            continue
        icon = "✓" if acc >= 0.9 else "✗"
        field_lines.append(f"  - {field}: {acc:.0%} {icon}")

    fields_section = "\n".join(field_lines)
    acc_str = f"{accuracy:.0%}" if accuracy is not None else "N/A"

    msg = (
        f"⚠️ <b>Connector Quality Alert</b>\n\n"
        f"🔴 <b>{html.escape(source)}</b>: accuracy {acc_str} (threshold {threshold:.0%})\n"
        f"{fields_section}\n\n"
        f"Last 7d: {sample_size} listings audited\n\n"
        f"Action: check {html.escape(source)} connector for HTML structure changes"
    )

    try:
        bot = Bot(token=settings.telegram_bot_token)
        message = await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=msg,
            parse_mode="HTML",
        )
        return {"status": "success", "message_id": message.message_id}
    except Exception as exc:
        logger.error("Failed to send connector quality alert: %s", exc)
        return {"status": "error", "error": str(exc)}


async def send_test_message(message: str = "Test message from Market Discovery") -> dict[str, Any]:
    """
    Send a test message to verify Telegram configuration.

    Args:
        message: Test message text

    Returns:
        Dict with send status
    """
    bot = _get_bot()
    if not bot:
        return {
            "status": "error",
            "error": "Telegram bot not available",
        }

    if not settings.telegram_chat_id:
        return {
            "status": "error",
            "error": "Telegram chat ID not configured",
        }

    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=message,
        )
        logger.info("Sent test Telegram message")
        return {
            "status": "success",
            "message": "Test message sent successfully",
        }
    except Exception as e:
        logger.error(f"Error sending test message: {e}")
        return {
            "status": "error",
            "error": str(e),
        }

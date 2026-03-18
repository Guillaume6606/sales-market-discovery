"""
Telegram alert service for sending opportunity notifications.
"""

import html
import os
from typing import Any

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
        # Format message
        product_name = html.escape(product_template.get("name", "Unknown Product"))
        brand = html.escape(product_template.get("brand", ""))
        listing_title = html.escape(listing.get("title", "No title"))
        listing_price = listing.get("price", 0)
        listing_url = listing.get("url", "")
        margin_pct = opportunity.get("margin_pct", 0)
        margin_abs = opportunity.get("margin_abs", 0)
        pmn = opportunity.get("pmn", 0)

        # Build message text
        message_text = f"""🎯 <b>Arbitrage Opportunity Found!</b>

📦 <b>Product:</b> {product_name}
{f"🏷️ <b>Brand:</b> {brand}" if brand else ""}

📋 <b>Listing:</b> {listing_title}
💰 <b>Price:</b> €{listing_price:.2f}
📊 <b>PMN:</b> €{pmn:.2f}

💵 <b>Margin:</b> {margin_pct:.1f}% (€{margin_abs:.2f})
{_confidence_badge(pmn_confidence)}
🔗 <a href="{listing_url}">View Listing</a>"""

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
            if screenshot_path and os.path.exists(screenshot_path):
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

"""
Telegram alert service for sending opportunity notifications.
"""

import os
from typing import Dict, Any, Optional
from pathlib import Path
from loguru import logger

from libs.common.settings import settings

try:
    from telegram import Bot
    from telegram.error import TelegramError, RetryAfter, TimedOut
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not available. Telegram alerts will be disabled.")


def _get_bot() -> Optional[Bot]:
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


def send_opportunity_alert(
    opportunity: Dict[str, Any],
    listing: Dict[str, Any],
    product_template: Dict[str, Any],
    screenshot_path: Optional[str] = None,
) -> Dict[str, Any]:
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
        product_name = product_template.get("name", "Unknown Product")
        brand = product_template.get("brand", "")
        listing_title = listing.get("title", "No title")
        listing_price = listing.get("price", 0)
        listing_url = listing.get("url", "")
        margin_pct = opportunity.get("margin_pct", 0)
        margin_abs = opportunity.get("margin_abs", 0)
        pmn = opportunity.get("pmn", 0)
        
        # Build message text
        message_text = f"""🎯 <b>Arbitrage Opportunity Found!</b>

📦 <b>Product:</b> {product_name}
{f'🏷️ <b>Brand:</b> {brand}' if brand else ''}

📋 <b>Listing:</b> {listing_title}
💰 <b>Price:</b> €{listing_price:.2f}
📊 <b>PMN:</b> €{pmn:.2f}

💵 <b>Margin:</b> {margin_pct:.1f}% (€{margin_abs:.2f})

🔗 <a href="{listing_url}">View Listing</a>"""
        
        # Send message
        try:
            if screenshot_path and os.path.exists(screenshot_path):
                # Send with photo
                with open(screenshot_path, 'rb') as photo:
                    message = bot.send_photo(
                        chat_id=settings.telegram_chat_id,
                        photo=photo,
                        caption=message_text,
                        parse_mode='HTML',
                        disable_web_page_preview=False,
                    )
            else:
                # Send text only
                message = bot.send_message(
                    chat_id=settings.telegram_chat_id,
                    text=message_text,
                    parse_mode='HTML',
                    disable_web_page_preview=False,
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


def send_test_message(message: str = "Test message from Market Discovery") -> Dict[str, Any]:
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
        bot.send_message(
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

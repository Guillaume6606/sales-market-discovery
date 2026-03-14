"""
Telegram Notification Service

This module provides Telegram bot integration for sending alerts about
verified arbitrage opportunities.
"""

import os
import logging
from typing import Dict, Any, Optional
from libs.common.log import logger

try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not available. Telegram notifications will be disabled.")


def send_alert(
    listing: Dict[str, Any],
    product: Dict[str, Any],
    analysis: Dict[str, Any]
) -> bool:
    """
    Send a Telegram alert about a verified listing opportunity.
    
    Args:
        listing: Dict with listing information (title, price, url, etc.)
        product: Dict with product information (name, target_description, etc.)
        analysis: Dict with LLM analysis results (score, reasoning, etc.)
        
    Returns:
        True if message was sent successfully, False otherwise
    """
    if not TELEGRAM_AVAILABLE:
        logger.warning("Telegram alert requested but python-telegram-bot not available")
        return False
    
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set. Skipping notification.")
        return False
    
    try:
        bot = Bot(token=bot_token)
        
        # Build message
        message = f"""🎯 <b>New Arbitrage Opportunity!</b>

<b>Product:</b> {product.get('name', 'Unknown')}
<b>Target:</b> {product.get('target_description', 'N/A')}

<b>Listing:</b>
• Title: {listing.get('title', 'N/A')}
• Price: €{listing.get('price', 0):.2f}
• Source: {listing.get('source', 'Unknown')}
• Condition: {listing.get('condition', 'N/A')}

<b>LLM Analysis:</b>
• Score: {analysis.get('score', 0):.1f}/100
• Verified: {'✅ Yes' if analysis.get('verified', False) else '❌ No'}
• Reasoning: {analysis.get('reasoning', 'N/A')[:200]}

<b>Link:</b> {listing.get('url', 'N/A')}
"""
        
        # Send message
        bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="HTML",
            disable_web_page_preview=False
        )
        
        logger.info(f"Telegram alert sent for listing {listing.get('obs_id', 'unknown')}")
        return True
        
    except TelegramError as e:
        logger.error(f"Telegram API error: {e}")
        return False
    except Exception as e:
        logger.error(f"Error sending Telegram alert: {e}", exc_info=True)
        return False

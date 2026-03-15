"""
Feedback router: Telegram webhook, manual feedback submission, and alert precision analytics.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from libs.common.db import get_db
from libs.common.models import AlertEvent, AlertFeedback
from libs.common.settings import settings

try:
    from telegram import Bot

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

router = APIRouter(tags=["feedback"])

VALID_FEEDBACK = {"interested", "not_interested", "purchased"}


class FeedbackCreate(BaseModel):
    feedback: str
    notes: str | None = None


def _verify_webhook_secret(request: Request) -> None:
    """Verify Telegram webhook secret if configured."""
    if not settings.telegram_webhook_secret:
        return
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != settings.telegram_webhook_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")


def _upsert_feedback(db: Session, alert_id: int, feedback: str, notes: str | None = None) -> str:
    """Atomic upsert of feedback for an alert. Returns 'created' or 'updated'."""
    existing = db.query(AlertFeedback).filter(AlertFeedback.alert_id == alert_id).first()
    if existing:
        existing.feedback = feedback
        existing.notes = notes if notes is not None else existing.notes
        return "updated"
    db.add(AlertFeedback(alert_id=alert_id, feedback=feedback, notes=notes))
    return "created"


# --------------------------------------------------------------------------- #
# Telegram webhook
# --------------------------------------------------------------------------- #


@router.post("/webhooks/telegram")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Handle Telegram callback_query from inline keyboard buttons."""
    _verify_webhook_secret(request)

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body") from exc

    callback_query = body.get("callback_query")
    if not callback_query:
        # Not a callback query (e.g. a regular message) — just acknowledge
        return {"status": "ok"}

    callback_data = callback_query.get("data", "")
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != "fb":
        logger.warning(f"Unexpected callback_data format: {callback_data}")
        return {"status": "ignored"}

    _, alert_id_str, action = parts

    try:
        alert_id = int(alert_id_str)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid alert_id") from exc

    if action not in VALID_FEEDBACK:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Invalid feedback: {action}")

    # Validate alert exists
    alert = db.query(AlertEvent).filter(AlertEvent.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Alert not found")

    _upsert_feedback(db, alert_id, action)
    db.commit()

    # Answer callback query and remove keyboard
    callback_query_id = callback_query.get("id")
    if TELEGRAM_AVAILABLE and settings.telegram_bot_token and callback_query_id:
        try:
            bot = Bot(token=settings.telegram_bot_token)
            await bot.answer_callback_query(callback_query_id, text=f"Feedback recorded: {action}")
            # Remove inline keyboard from original message
            message = callback_query.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            if chat_id and message_id:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=message_id, reply_markup=None
                )
        except Exception as e:
            logger.warning(f"Failed to answer callback query: {e}")

    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Manual feedback CRUD
# --------------------------------------------------------------------------- #


@router.post("/alerts/events/{alert_id}/feedback", status_code=status.HTTP_201_CREATED)
def create_feedback(
    alert_id: int,
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Submit feedback for an alert event."""
    if payload.feedback not in VALID_FEEDBACK:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"feedback must be one of: {', '.join(sorted(VALID_FEEDBACK))}",
        )

    alert = db.query(AlertEvent).filter(AlertEvent.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Alert not found")

    result = _upsert_feedback(db, alert_id, payload.feedback, payload.notes)
    db.commit()
    return {"status": result, "alert_id": alert_id, "feedback": payload.feedback}


@router.get("/alerts/events/{alert_id}/feedback")
def get_feedback(
    alert_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Get feedback for an alert event."""
    alert = db.query(AlertEvent).filter(AlertEvent.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Alert not found")

    fb = db.query(AlertFeedback).filter(AlertFeedback.alert_id == alert_id).first()
    if not fb:
        return {"alert_id": alert_id, "feedback": None}

    return {
        "alert_id": alert_id,
        "feedback": {
            "feedback_id": str(fb.feedback_id),
            "feedback": fb.feedback,
            "notes": fb.notes,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
            "updated_at": fb.updated_at.isoformat() if fb.updated_at else None,
        },
    }


# --------------------------------------------------------------------------- #
# Alert precision analytics
# --------------------------------------------------------------------------- #


@router.get("/analytics/alert-precision")
def alert_precision(
    days: int = Query(30, description="Look-back period in days"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Compute alert precision metrics over the given period."""
    since = datetime.now(UTC) - timedelta(days=days)

    # Non-suppressed alerts in period
    total_alerts = (
        db.query(func.count(AlertEvent.alert_id))
        .filter(
            AlertEvent.sent_at >= since,
            AlertEvent.suppressed.is_(False),
        )
        .scalar()
        or 0
    )

    # Single query for all feedback counts
    row = (
        db.query(
            func.count(func.distinct(AlertFeedback.alert_id)).label("total"),
            func.count(
                case((AlertFeedback.feedback == "interested", AlertFeedback.alert_id))
            ).label("interested"),
            func.count(
                case((AlertFeedback.feedback == "not_interested", AlertFeedback.alert_id))
            ).label("not_interested"),
            func.count(case((AlertFeedback.feedback == "purchased", AlertFeedback.alert_id))).label(
                "purchased"
            ),
        )
        .join(AlertEvent, AlertEvent.alert_id == AlertFeedback.alert_id)
        .filter(
            AlertEvent.sent_at >= since,
            AlertEvent.suppressed.is_(False),
        )
        .one()
    )

    total_with_feedback = row.total or 0
    interested_count = row.interested or 0
    not_interested_count = row.not_interested or 0
    purchased_count = row.purchased or 0

    precision = (
        round((interested_count + purchased_count) / total_with_feedback, 4)
        if total_with_feedback > 0
        else None
    )
    feedback_rate = round(total_with_feedback / total_alerts, 4) if total_alerts > 0 else None

    return {
        "days": days,
        "total_alerts": total_alerts,
        "total_with_feedback": total_with_feedback,
        "feedback_rate": feedback_rate,
        "interested_count": interested_count,
        "not_interested_count": not_interested_count,
        "purchased_count": purchased_count,
        "precision": precision,
    }

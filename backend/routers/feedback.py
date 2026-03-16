"""
Feedback router: Telegram webhook, manual feedback submission, and alert precision analytics.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.common.db import get_db
from libs.common.models import VALID_FEEDBACK_VALUES, AlertEvent, AlertFeedback
from libs.common.settings import settings

try:
    from telegram import Bot  # noqa: F401 (used for type reference)

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

from libs.common.telegram_service import _get_bot

router = APIRouter(tags=["feedback"])

VALID_FEEDBACK = set(VALID_FEEDBACK_VALUES)


class FeedbackCreate(BaseModel):
    feedback: str
    notes: str | None = None


class FeedbackUpdate(BaseModel):
    feedback: str | None = None
    notes: str | None = None
    profit: float | None = Field(None, ge=0)


def _verify_webhook_secret(request: Request) -> None:
    """Verify Telegram webhook secret if configured."""
    if not settings.telegram_webhook_secret:
        return
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != settings.telegram_webhook_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")


def _upsert_feedback(db: Session, alert_id: int, feedback: str, notes: str | None = None) -> str:
    """Atomic upsert using INSERT ... ON CONFLICT DO UPDATE (PostgreSQL)."""
    values: dict[str, Any] = {"alert_id": alert_id, "feedback": feedback}
    if notes is not None:
        values["notes"] = notes

    stmt = pg_insert(AlertFeedback).values(**values)
    update_cols: dict[str, Any] = {"feedback": stmt.excluded.feedback}
    if notes is not None:
        update_cols["notes"] = stmt.excluded.notes
    stmt = stmt.on_conflict_do_update(
        index_elements=["alert_id"],
        set_=update_cols,
    )
    db.execute(stmt)
    # ON CONFLICT DO UPDATE always yields rowcount==1 regardless of insert vs update,
    # so we cannot distinguish between the two. Return "upserted" instead.
    return "upserted"


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
    bot = _get_bot() if callback_query_id else None
    if bot and callback_query_id:
        try:
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
            "profit": float(fb.profit) if fb.profit is not None else None,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
            "updated_at": fb.updated_at.isoformat() if fb.updated_at else None,
        },
    }


@router.patch("/alerts/feedback/{feedback_id}")
def update_feedback(
    feedback_id: str,
    payload: FeedbackUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Update feedback fields (feedback, notes, profit)."""
    fb = db.query(AlertFeedback).filter(AlertFeedback.feedback_id == feedback_id).first()
    if not fb:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Feedback not found")

    if payload.feedback is not None:
        if payload.feedback not in VALID_FEEDBACK:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"feedback must be one of: {', '.join(sorted(VALID_FEEDBACK))}",
            )
        fb.feedback = payload.feedback
    if payload.notes is not None:
        fb.notes = payload.notes
    if payload.profit is not None:
        fb.profit = payload.profit

    db.commit()
    db.refresh(fb)

    return {
        "feedback_id": str(fb.feedback_id),
        "alert_id": fb.alert_id,
        "feedback": fb.feedback,
        "notes": fb.notes,
        "profit": float(fb.profit) if fb.profit is not None else None,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
        "updated_at": fb.updated_at.isoformat() if fb.updated_at else None,
    }


# --------------------------------------------------------------------------- #
# Alert precision analytics
# --------------------------------------------------------------------------- #


def compute_precision_summary(db: Session, days: int = 30) -> dict[str, Any]:
    """Compute alert precision metrics over the given period.

    Extracted as a helper so it can be reused in the health overview.
    """
    since = datetime.now(UTC) - timedelta(days=days)

    total_alerts = (
        db.query(func.count(AlertEvent.alert_id))
        .filter(
            AlertEvent.sent_at >= since,
            AlertEvent.suppressed.is_(False),
        )
        .scalar()
        or 0
    )

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
        .one_or_none()
    )

    if row is None:
        return {
            "days": days,
            "total_alerts": total_alerts,
            "total_with_feedback": 0,
            "feedback_rate": 0.0 if total_alerts > 0 else None,
            "interested_count": 0,
            "not_interested_count": 0,
            "purchased_count": 0,
            "precision": None,
        }

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


@router.get("/analytics/alert-precision")
def alert_precision(
    days: int = Query(30, ge=1, description="Look-back period in days"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Compute alert precision metrics over the given period."""
    return compute_precision_summary(db, days)

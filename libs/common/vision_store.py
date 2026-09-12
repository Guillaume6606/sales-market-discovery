"""PostgreSQL claims and reservations; unknown charges stay reserved for reconciliation."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from libs.common.db import SessionLocal
from libs.common.models import VisionAttempt, VisionBudget, VisionRequest


class ClaimLostError(RuntimeError):
    pass


class VisionStore:
    def __init__(self, session_factory=SessionLocal) -> None:
        self.session_factory = session_factory

    def claim(self, key: str) -> tuple[str | None, dict | None]:
        owner = uuid4().hex
        with self.session_factory.begin() as db:
            claimed = db.execute(
                insert(VisionRequest)
                .values(
                    request_key=key,
                    status="pending",
                    owner_token=owner,
                    lease_expires_at=func.now() + text("INTERVAL '10 minutes'"),
                )
                .on_conflict_do_update(
                    index_elements=[VisionRequest.request_key],
                    set_={
                        "status": "pending",
                        "result": None,
                        "created_at": func.now(),
                        "owner_token": owner,
                        "lease_expires_at": func.now() + text("INTERVAL '10 minutes'"),
                    },
                    where=(
                        (
                            VisionRequest.status.in_(["error", "budget_exhausted"])
                            & (VisionRequest.created_at < func.now() - text("INTERVAL '5 minutes'"))
                        )
                        | (
                            (VisionRequest.status == "pending")
                            & (VisionRequest.lease_expires_at < func.now())
                        )
                    ),
                )
                .returning(VisionRequest.request_key)
            ).scalar_one_or_none()
            if claimed:
                return owner, None
            row = db.get(VisionRequest, key)
            return None, row.result

    def reserve(
        self, key: str, owner: str, currency: str, amount: Decimal, limit: Decimal
    ) -> int | None:
        period = datetime.now(UTC).strftime("%Y-%m")
        with self.session_factory.begin() as db:
            claim = db.execute(
                select(VisionRequest)
                .where(
                    VisionRequest.request_key == key,
                    VisionRequest.owner_token == owner,
                    VisionRequest.status == "pending",
                    VisionRequest.lease_expires_at > func.now(),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if claim is None:
                raise ClaimLostError("Vision claim expired or superseded")
            claim.lease_expires_at = func.now() + text("INTERVAL '10 minutes'")
            db.execute(
                insert(VisionBudget)
                .values(period=period, currency=currency, reserved=0)
                .on_conflict_do_nothing()
            )
            row = db.execute(
                update(VisionBudget)
                .where(
                    VisionBudget.period == period,
                    VisionBudget.currency == currency,
                    VisionBudget.reserved + amount <= limit,
                )
                .values(reserved=VisionBudget.reserved + amount)
                .returning(VisionBudget.period)
            ).scalar_one_or_none()
            if row is None:
                return None
            attempt = VisionAttempt(
                request_key=key, period=period, currency=currency, reserved=amount
            )
            db.add(attempt)
            db.flush()
            return attempt.attempt_id

    def record_attempt(self, attempt_id: int, metadata: dict, actual: Decimal | None) -> None:
        with self.session_factory.begin() as db:
            attempt = db.execute(
                select(VisionAttempt)
                .where(VisionAttempt.attempt_id == attempt_id)
                .with_for_update()
            ).scalar_one()
            if actual is not None and (not actual.is_finite() or actual < 0):
                raise ValueError("Actual cost must be finite and nonnegative")
            if attempt.reconciled_at is not None:
                return
            attempt.metadata_json = {**(attempt.metadata_json or {}), **metadata}
            if actual is not None:
                attempt.actual_cost = actual
                attempt.reconciled_at = func.now()
                db.execute(
                    update(VisionBudget)
                    .where(
                        VisionBudget.period == attempt.period,
                        VisionBudget.currency == attempt.currency,
                    )
                    .values(reserved=VisionBudget.reserved + actual - attempt.reserved)
                )

    def finish(self, key: str, owner: str, result: dict) -> None:
        with self.session_factory.begin() as db:
            db.execute(
                update(VisionRequest)
                .where(
                    VisionRequest.request_key == key,
                    VisionRequest.owner_token == owner,
                    VisionRequest.status == "pending",
                    VisionRequest.lease_expires_at > func.now(),
                )
                .values(status=result["status"], result=result, created_at=func.now())
            )

    def health_summary(self) -> dict:
        period = datetime.now(UTC).strftime("%Y-%m")
        with self.session_factory() as db:
            budgets = (
                db.execute(select(VisionBudget).where(VisionBudget.period == period))
                .scalars()
                .all()
            )
            counts = db.execute(
                select(VisionRequest.status, func.count()).group_by(VisionRequest.status)
            ).all()
            return {
                "period": period,
                "reserved_by_currency": {row.currency: str(row.reserved) for row in budgets},
                "requests_by_status": dict(counts),
            }

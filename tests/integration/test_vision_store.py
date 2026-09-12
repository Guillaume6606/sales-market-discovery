from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from libs.common.models import VisionBudget
from libs.common.vision_store import VisionStore


def test_atomic_budget_limit_and_idempotent_claim(integration_db):
    store = VisionStore(sessionmaker(bind=integration_db.bind))
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(store.claim, ["same-key"] * 4))
    assert sum(bool(claimed) for claimed, _ in claims) == 1
    owner = next(owner for owner, _ in claims if owner)
    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = list(
            pool.map(
                lambda _: store.reserve("same-key", owner, "EUR", Decimal("0.6"), Decimal("1")),
                range(4),
            )
        )
    assert sum(attempt is not None for attempt in attempts) == 1
    assert integration_db.execute(select(VisionBudget.reserved)).scalar_one() == Decimal("0.6")
    attempt = next(value for value in attempts if value is not None)
    store.record_attempt(attempt, {"input_tokens": None}, None)
    assert integration_db.execute(select(VisionBudget.reserved)).scalar_one() == Decimal("0.6")


def test_reconciliation_is_idempotent_and_currencies_separate(integration_db):
    store = VisionStore(sessionmaker(bind=integration_db.bind))
    owner, _ = store.claim("a")
    euro = store.reserve("a", owner, "EUR", Decimal("1"), Decimal("1"))
    assert store.reserve("a", owner, "USD", Decimal("1"), Decimal("1")) is not None
    store.record_attempt(euro, {"input_tokens": 1}, Decimal("0.1"))
    store.record_attempt(euro, {"input_tokens": 1}, Decimal("0.1"))
    assert integration_db.execute(
        select(VisionBudget.reserved).where(VisionBudget.currency == "EUR")
    ).scalar_one() == Decimal("0.1")


def test_stale_claim_recovers_with_fencing_and_keeps_possible_charge(integration_db):
    import pytest
    from sqlalchemy import func, text, update

    from libs.common.models import VisionRequest
    from libs.common.vision_store import ClaimLostError

    store = VisionStore(sessionmaker(bind=integration_db.bind))
    old_owner, _ = store.claim("stale")
    store.reserve("stale", old_owner, "EUR", Decimal("0.4"), Decimal("1"))
    integration_db.execute(
        update(VisionRequest)
        .where(VisionRequest.request_key == "stale")
        .values(lease_expires_at=func.now() - text("INTERVAL '1 minute'"))
    )
    integration_db.commit()
    new_owner, _ = store.claim("stale")
    assert new_owner and new_owner != old_owner
    store.finish("stale", old_owner, {"status": "completed"})
    assert store.claim("stale") == (None, None)
    with pytest.raises(ClaimLostError):
        store.reserve("stale", old_owner, "EUR", Decimal("0.1"), Decimal("1"))
    store.finish("stale", new_owner, {"status": "completed", "result": "new"})
    assert store.claim("stale")[1]["result"] == "new"
    assert integration_db.execute(select(VisionBudget.reserved)).scalar_one() == Decimal("0.4")


def test_unknown_usage_can_later_reconcile_exactly_once(integration_db):
    from libs.common.models import VisionAttempt

    store = VisionStore(sessionmaker(bind=integration_db.bind))
    owner, _ = store.claim("late-billing")
    attempt = store.reserve("late-billing", owner, "USD", Decimal("1"), Decimal("1"))
    store.record_attempt(attempt, {"input_tokens": None}, None)
    store.record_attempt(attempt, {"input_tokens": 100}, Decimal("0.2"))
    store.record_attempt(attempt, {"input_tokens": 100}, Decimal("0.2"))
    assert integration_db.execute(select(VisionBudget.reserved)).scalar_one() == Decimal("0.2")
    row = integration_db.get(VisionAttempt, attempt)
    assert row.actual_cost == Decimal("0.2")
    assert row.reconciled_at is not None
    assert row.metadata_json["input_tokens"] == 100

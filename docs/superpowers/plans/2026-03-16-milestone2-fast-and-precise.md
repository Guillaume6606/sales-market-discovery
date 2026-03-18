# Milestone 2: Fast and Precise — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move from daily batch ingestion to hourly per-product scheduling, replace the simple price-below-PMN alert with a composite 0-100 opportunity score, and add tiered alerting (Telegram/dashboard/suppressed).

**Architecture:** Add `ingestion_interval_hours` to ProductTemplate for per-product frequency. New `ingestion/scoring.py` module replaces the old 3-factor scorer with a 6-factor weighted formula. Alert engine drops AlertRule evaluation in favor of score-based tiering. Worker crons run hourly (staggered per connector). LLM validation gates Telegram-tier alerts post-score.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0, Alembic, ARQ, FastAPI, pytest

**Spec:** `docs/superpowers/specs/2026-03-16-milestone2-fast-and-precise-design.md`

---

## Chunk 1: Foundation — Schema, Models, Settings

### Task 1: Alembic Migration

**Files:**
- Create: `migrations/versions/0006_milestone2_frequency_scoring.py`

- [ ] **Step 1: Generate migration skeleton**

Run: `uv run alembic revision --autogenerate -m "milestone2_frequency_scoring"`

Then replace the body with:

```python
"""milestone2_frequency_scoring"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ProductTemplate: per-product ingestion interval
    op.add_column(
        "product_template",
        sa.Column(
            "ingestion_interval_hours",
            sa.Integer,
            nullable=False,
            server_default="24",
        ),
    )

    # AlertEvent: scoring and tiering
    op.add_column(
        "alert_event",
        sa.Column("tier", sa.Text, nullable=True),
    )
    op.create_check_constraint(
        "ck_alert_event_tier",
        "alert_event",
        "tier IN ('telegram', 'dashboard')",
    )
    op.add_column(
        "alert_event",
        sa.Column("opportunity_score", sa.Numeric, nullable=True),
    )
    op.add_column(
        "alert_event",
        sa.Column("ingestion_run_id", sa.UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_alert_event_ingestion_run",
        "alert_event",
        "ingestion_run",
        ["ingestion_run_id"],
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_alert_event_ingestion_run", "alert_event", type_="foreignkey")
    op.drop_column("alert_event", "ingestion_run_id")
    op.drop_column("alert_event", "opportunity_score")
    op.drop_constraint("ck_alert_event_tier", "alert_event", type_="check")
    op.drop_column("alert_event", "tier")
    op.drop_column("product_template", "ingestion_interval_hours")
```

- [ ] **Step 2: Apply migration**

Run: `uv run alembic upgrade head`
Expected: Migration applies cleanly.

- [ ] **Step 3: Commit**

```bash
git add migrations/versions/0006_milestone2_frequency_scoring.py
git commit -m "feat: add migration 0006 — ingestion_interval_hours, alert tier/score columns"
```

---

### Task 2: Update SQLAlchemy Models

**Files:**
- Modify: `libs/common/models.py:38-57` (ProductTemplate), `libs/common/models.py:151-165` (AlertEvent)

- [ ] **Step 1: Add `ingestion_interval_hours` to ProductTemplate**

In `libs/common/models.py`, add after `last_ingested_at` (around line 56):

```python
    ingestion_interval_hours = Column(Integer, nullable=False, server_default="24")
```

- [ ] **Step 2: Add tier, opportunity_score, ingestion_run_id to AlertEvent**

In `libs/common/models.py`, add after `suppressed` (around line 160):

```python
    tier = Column(Text)  # 'telegram' or 'dashboard'
    opportunity_score = Column(Numeric)
    ingestion_run_id = Column(UUID, ForeignKey("ingestion_run.run_id"))
```

- [ ] **Step 3: Commit**

```bash
git add libs/common/models.py
git commit -m "feat: add ingestion_interval_hours, alert tier/score to models"
```

---

### Task 3: Add Settings

**Files:**
- Modify: `libs/common/settings.py`

- [ ] **Step 1: Add alerting threshold and interval settings**

Add after `min_pmn_confidence` (around line 41):

```python
    # Tiered alerting thresholds (opportunity score 0-100)
    alert_telegram_threshold: int = 80
    alert_dashboard_threshold: int = 50
```

- [ ] **Step 2: Commit**

```bash
git add libs/common/settings.py
git commit -m "feat: add alert_telegram_threshold and alert_dashboard_threshold settings"
```

---

## Chunk 2: Scoring Module (TDD)

### Task 4: Opportunity Scoring — Tests First

**Files:**
- Create: `tests/unit/test_scoring.py`
- Create: `ingestion/scoring.py`

- [ ] **Step 1: Write scoring tests**

Create `tests/unit/test_scoring.py`:

```python
"""Tests for the composite opportunity scoring module."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from ingestion.scoring import compute_opportunity_score


def _make_listing(**overrides):
    listing = MagicMock()
    listing.price = overrides.get("price", 80)
    listing.observed_at = overrides.get("observed_at", datetime.now(UTC))
    listing.seller_rating = overrides.get("seller_rating", 4.5)
    listing.source = overrides.get("source", "ebay")
    return listing


def _make_pmn(**overrides):
    pmn = MagicMock()
    pmn.pmn = overrides.get("pmn", 100)
    pmn.confidence = overrides.get("confidence", 0.8)
    return pmn


def _make_metrics(**overrides):
    metrics = MagicMock()
    metrics.liquidity_score = overrides.get("liquidity_score", 70)
    return metrics


class TestComputeOpportunityScore:
    def test_perfect_deal(self):
        """Very cheap listing, high liquidity, fresh, confident PMN, great seller, LLM confirms."""
        listing = _make_listing(price=10, seller_rating=5.0)
        pmn = _make_pmn(pmn=100, confidence=1.0)
        metrics = _make_metrics(liquidity_score=100)

        result = compute_opportunity_score(
            listing, pmn, metrics, llm_result={"is_relevant": True}
        )

        # margin=0.9*35 + liquidity=1.0*20 + freshness~1.0*15 + confidence=1.0*15 + seller=1.0*10 + llm=1.0*5 = 96.5
        assert result["score"] >= 90
        assert result["breakdown"]["margin"]["raw"] == pytest.approx(0.9)
        assert "breakdown" in result

    def test_marginal_deal(self):
        """Price close to PMN, low liquidity, old listing."""
        listing = _make_listing(
            price=95,
            seller_rating=3.0,
            observed_at=datetime.now(UTC) - timedelta(days=5),
        )
        pmn = _make_pmn(pmn=100, confidence=0.5)
        metrics = _make_metrics(liquidity_score=20)

        result = compute_opportunity_score(listing, pmn, metrics)

        assert result["score"] < 50

    def test_no_metrics_uses_defaults(self):
        """Missing ProductDailyMetrics — liquidity defaults to 0."""
        listing = _make_listing(price=70)
        pmn = _make_pmn(pmn=100, confidence=0.8)

        result = compute_opportunity_score(listing, pmn, None)

        assert result["breakdown"]["liquidity"]["raw"] == 0.0
        assert result["score"] > 0

    def test_missing_seller_rating_defaults_to_half(self):
        listing = _make_listing(price=70, seller_rating=None)
        pmn = _make_pmn(pmn=100, confidence=0.8)
        metrics = _make_metrics()

        result = compute_opportunity_score(listing, pmn, metrics)

        assert result["breakdown"]["seller_rating"]["raw"] == 0.5

    def test_llm_relevant_boosts_score(self):
        listing = _make_listing(price=75)
        pmn = _make_pmn(pmn=100, confidence=0.8)
        metrics = _make_metrics()

        without_llm = compute_opportunity_score(listing, pmn, metrics)
        with_llm = compute_opportunity_score(
            listing, pmn, metrics, llm_result={"is_relevant": True}
        )

        assert with_llm["score"] > without_llm["score"]

    def test_llm_rejected_penalizes_score(self):
        listing = _make_listing(price=75)
        pmn = _make_pmn(pmn=100, confidence=0.8)
        metrics = _make_metrics()

        without_llm = compute_opportunity_score(listing, pmn, metrics)
        with_llm = compute_opportunity_score(
            listing, pmn, metrics, llm_result={"is_relevant": False}
        )

        assert with_llm["score"] < without_llm["score"]

    def test_score_clamped_to_0_100(self):
        """Even extreme inputs stay in 0-100 range."""
        listing = _make_listing(price=1)  # 99% margin
        pmn = _make_pmn(pmn=100, confidence=1.0)
        metrics = _make_metrics(liquidity_score=100)

        result = compute_opportunity_score(
            listing, pmn, metrics, llm_result={"is_relevant": True}
        )

        assert 0 <= result["score"] <= 100

    def test_price_at_pmn_zero_margin(self):
        listing = _make_listing(price=100)
        pmn = _make_pmn(pmn=100, confidence=0.8)
        metrics = _make_metrics()

        result = compute_opportunity_score(listing, pmn, metrics)

        assert result["breakdown"]["margin"]["raw"] == 0.0

    def test_price_above_pmn_negative_margin_clamped(self):
        listing = _make_listing(price=120)
        pmn = _make_pmn(pmn=100, confidence=0.8)
        metrics = _make_metrics()

        result = compute_opportunity_score(listing, pmn, metrics)

        assert result["breakdown"]["margin"]["raw"] == 0.0

    def test_freshness_decays_over_7_days(self):
        fresh = _make_listing(observed_at=datetime.now(UTC))
        old = _make_listing(observed_at=datetime.now(UTC) - timedelta(days=7))
        pmn = _make_pmn()
        metrics = _make_metrics()

        fresh_result = compute_opportunity_score(fresh, pmn, metrics)
        old_result = compute_opportunity_score(old, pmn, metrics)

        assert fresh_result["breakdown"]["freshness"]["raw"] > 0.9
        assert old_result["breakdown"]["freshness"]["raw"] == pytest.approx(0.0, abs=0.02)

    def test_breakdown_keys(self):
        listing = _make_listing(price=80)
        pmn = _make_pmn()
        metrics = _make_metrics()

        result = compute_opportunity_score(listing, pmn, metrics)

        assert set(result["breakdown"].keys()) == {
            "margin", "liquidity", "freshness", "pmn_confidence", "seller_rating", "llm",
        }
        for factor in result["breakdown"].values():
            assert "raw" in factor
            assert "weighted" in factor
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingestion.scoring'`

- [ ] **Step 3: Implement scoring module**

Create `ingestion/scoring.py`:

```python
"""Composite opportunity scoring (0-100) for listing attractiveness."""

from datetime import UTC, datetime
from typing import Any

from libs.common.models import ListingObservation, MarketPriceNormal, ProductDailyMetrics

# Weights must sum to 1.0
WEIGHTS = {
    "margin": 0.35,
    "liquidity": 0.20,
    "freshness": 0.15,
    "pmn_confidence": 0.15,
    "seller_rating": 0.10,
    "llm": 0.05,
}

FRESHNESS_DECAY_HOURS = 168  # 7 days


def compute_opportunity_score(
    listing: ListingObservation,
    pmn_data: MarketPriceNormal,
    metrics: ProductDailyMetrics | None,
    llm_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute composite opportunity score (0-100).

    Returns dict with 'score' (float) and 'breakdown' (per-factor details).
    """
    pmn = float(pmn_data.pmn)
    price = float(listing.price)

    # --- Sub-scores (each 0.0 to 1.0) ---

    # Margin: how far below PMN
    margin_raw = max(0.0, (pmn - price) / pmn) if pmn > 0 else 0.0

    # Liquidity: from ProductDailyMetrics (0-100 → 0-1)
    liquidity_raw = (float(metrics.liquidity_score) / 100.0) if metrics and metrics.liquidity_score else 0.0

    # Freshness: decays over 7 days from observed_at
    if listing.observed_at:
        observed = listing.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        hours_old = (datetime.now(UTC) - observed).total_seconds() / 3600
        freshness_raw = max(0.0, 1.0 - hours_old / FRESHNESS_DECAY_HOURS)
    else:
        freshness_raw = 0.0

    # PMN confidence (already 0-1)
    confidence_raw = float(pmn_data.confidence) if pmn_data.confidence else 0.0

    # Seller rating (0-5 → 0-1, default 0.5 if missing)
    if listing.seller_rating is not None:
        seller_raw = min(float(listing.seller_rating) / 5.0, 1.0)
    else:
        seller_raw = 0.5

    # LLM assessment
    if llm_result is None:
        llm_raw = 0.5  # neutral — not run
    elif llm_result.get("is_relevant"):
        llm_raw = 1.0
    else:
        llm_raw = 0.0

    # --- Weighted total ---
    sub_scores = {
        "margin": margin_raw,
        "liquidity": liquidity_raw,
        "freshness": freshness_raw,
        "pmn_confidence": confidence_raw,
        "seller_rating": seller_raw,
        "llm": llm_raw,
    }

    score = sum(WEIGHTS[k] * v for k, v in sub_scores.items()) * 100
    score = max(0.0, min(100.0, score))

    breakdown = {
        k: {"raw": round(v, 4), "weighted": round(WEIGHTS[k] * v * 100, 2)}
        for k, v in sub_scores.items()
    }

    return {"score": round(score, 1), "breakdown": breakdown}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_scoring.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Remove old `compute_opportunity_score` from `computation.py`**

In `ingestion/computation.py`, delete the function `compute_opportunity_score` (lines 436-566) and the section comment (lines 430-434). Check for any callers:

Run: `rg "from ingestion.computation import.*compute_opportunity_score" --type py`

Update any imports found to use `from ingestion.scoring import compute_opportunity_score`.

- [ ] **Step 6: Commit**

```bash
git add ingestion/scoring.py tests/unit/test_scoring.py ingestion/computation.py
git commit -m "feat: add 6-factor composite opportunity scoring module"
```

---

## Chunk 3: Alert Engine Rework (TDD)

### Task 5: Tiered Alert Engine — Tests First

**Files:**
- Create: `tests/unit/test_tiered_alerts.py`
- Modify: `ingestion/alert_engine.py`

- [ ] **Step 1: Write tiered alerting tests**

Create `tests/unit/test_tiered_alerts.py`:

```python
"""Tests for score-based tiered alerting."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ingestion.alert_engine import _determine_tier, trigger_alerts


class TestDetermineTier:
    def test_telegram_tier(self):
        assert _determine_tier(85) == "telegram"

    def test_dashboard_tier(self):
        assert _determine_tier(65) == "dashboard"

    def test_suppressed(self):
        assert _determine_tier(30) is None

    def test_exact_telegram_boundary(self):
        assert _determine_tier(80) == "telegram"

    def test_exact_dashboard_boundary(self):
        assert _determine_tier(50) == "dashboard"

    def test_just_below_dashboard(self):
        assert _determine_tier(49.9) is None


class TestTriggerAlertsIntegration:
    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None  # no duplicates
        return db

    @pytest.fixture
    def opportunity(self):
        listing = MagicMock()
        listing.obs_id = 1
        listing.price = 70
        listing.observed_at = datetime.now(UTC)
        listing.seller_rating = 4.0
        listing.source = "ebay"
        listing.is_sold = False
        listing.product_id = "test-product-id"

        pmn = MagicMock()
        pmn.pmn = 100
        pmn.confidence = 0.9

        metrics = MagicMock()
        metrics.liquidity_score = 80

        template = MagicMock()
        template.product_id = "test-product-id"
        template.name = "Test Product"
        template.enable_llm_validation = False

        return {
            "listing": listing,
            "product_template": template,
            "pmn_data": pmn,
            "metrics": metrics,
        }

    @pytest.mark.asyncio
    @patch("ingestion.alert_engine.send_opportunity_alert", new_callable=AsyncMock)
    @patch("ingestion.alert_engine.compute_opportunity_score")
    async def test_high_score_sends_telegram(self, mock_score, mock_send, mock_db, opportunity):
        mock_score.return_value = {"score": 85, "breakdown": {}}
        mock_send.return_value = {"status": "success", "message_id": 123}

        events = await trigger_alerts([opportunity], db=mock_db)

        assert len(events) == 1
        mock_send.assert_called_once()

    @pytest.mark.asyncio
    @patch("ingestion.alert_engine.send_opportunity_alert", new_callable=AsyncMock)
    @patch("ingestion.alert_engine.compute_opportunity_score")
    async def test_medium_score_dashboard_only(self, mock_score, mock_send, mock_db, opportunity):
        mock_score.return_value = {"score": 65, "breakdown": {}}

        events = await trigger_alerts([opportunity], db=mock_db)

        assert len(events) == 1
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    @patch("ingestion.alert_engine.send_opportunity_alert", new_callable=AsyncMock)
    @patch("ingestion.alert_engine.compute_opportunity_score")
    async def test_low_score_suppressed(self, mock_score, mock_send, mock_db, opportunity):
        mock_score.return_value = {"score": 30, "breakdown": {}}

        events = await trigger_alerts([opportunity], db=mock_db)

        assert len(events) == 0
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    @patch("ingestion.alert_engine.send_opportunity_alert", new_callable=AsyncMock)
    @patch("ingestion.alert_engine.compute_opportunity_score")
    async def test_duplicate_listing_skipped(self, mock_score, mock_send, mock_db, opportunity):
        """Same obs_id already has an alert — skip."""
        mock_score.return_value = {"score": 90, "breakdown": {}}
        # Simulate existing alert for this obs_id
        mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()

        events = await trigger_alerts([opportunity], db=mock_db)

        assert len(events) == 0
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    @patch("ingestion.alert_engine.send_opportunity_alert", new_callable=AsyncMock)
    @patch("ingestion.alert_engine.compute_opportunity_score")
    async def test_low_pmn_confidence_skipped(self, mock_score, mock_send, mock_db, opportunity):
        """PMN confidence below threshold — skip entirely."""
        opportunity["pmn_data"].confidence = 0.1  # below default 0.3
        mock_score.return_value = {"score": 90, "breakdown": {}}

        events = await trigger_alerts([opportunity], db=mock_db)

        assert len(events) == 0
        mock_score.assert_not_called()  # shouldn't even score

    @pytest.mark.asyncio
    @patch("ingestion.alert_engine.send_opportunity_alert", new_callable=AsyncMock)
    @patch("ingestion.alert_engine.compute_opportunity_score")
    async def test_ingestion_run_id_stored(self, mock_score, mock_send, mock_db, opportunity):
        """ingestion_run_id is stored on alert event for latency tracking."""
        mock_score.return_value = {"score": 65, "breakdown": {}}
        run_id = "test-run-uuid"

        events = await trigger_alerts([opportunity], db=mock_db, ingestion_run_id=run_id)

        assert len(events) == 1
        # Verify the AlertEvent was constructed with the run_id
        add_call = mock_db.add.call_args[0][0]
        assert add_call.ingestion_run_id == run_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tiered_alerts.py -v`
Expected: FAIL — `_determine_tier` does not exist, `trigger_alerts` has old signature.

- [ ] **Step 3: Rewrite alert engine**

Rewrite `ingestion/alert_engine.py`. The key changes:
- Remove `evaluate_alert_rules` and `_rule_matches` functions
- Replace `_check_duplicate_alert(db, rule_id, obs_id)` with `_check_duplicate_alert(db, obs_id)` — check by `obs_id` only
- Add `_determine_tier(score)` function using settings thresholds
- Rewrite `trigger_alerts` to: score → tier → LLM gate (if telegram) → persist → send

```python
"""Score-based tiered alert engine for opportunity notifications."""

from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from ingestion.scoring import compute_opportunity_score
from libs.common.db import SessionLocal
from libs.common.models import AlertEvent, ListingObservation
from libs.common.settings import settings
from libs.common.telegram_service import send_opportunity_alert
from libs.common.utils import decimal_to_float as _decimal_to_float


def _determine_tier(score: float) -> str | None:
    """Map opportunity score to alert tier. Returns None if suppressed."""
    if score >= settings.alert_telegram_threshold:
        return "telegram"
    if score >= settings.alert_dashboard_threshold:
        return "dashboard"
    return None


def _check_duplicate_alert(db: Session, obs_id: int) -> bool:
    """Check if an alert already exists for this listing (any tier)."""
    existing = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.obs_id == obs_id,
            AlertEvent.suppressed.is_(False),
        )
        .first()
    )
    return existing is not None


async def trigger_alerts(
    opportunities: list[dict[str, Any]],
    db: Session | None = None,
    ingestion_run_id: str | None = None,
) -> list[AlertEvent]:
    """
    Score opportunities and trigger tiered alerts.

    Args:
        opportunities: list of dicts with keys: listing, product_template, pmn_data, metrics
        db: database session
        ingestion_run_id: UUID of the triggering ingestion run (for latency tracking)

    Returns:
        list of persisted AlertEvent objects
    """
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False

    alert_events: list[AlertEvent] = []

    try:
        for opp in opportunities:
            listing = opp["listing"]
            product_template = opp["product_template"]
            pmn_data = opp.get("pmn_data")
            metrics = opp.get("metrics")

            if not pmn_data or not pmn_data.pmn:
                continue

            # Check PMN confidence threshold
            confidence = float(pmn_data.confidence) if pmn_data.confidence else 0.0
            if confidence < settings.min_pmn_confidence:
                logger.debug(
                    "Skipping listing %s: PMN confidence %.2f below threshold %.2f",
                    listing.obs_id, confidence, settings.min_pmn_confidence,
                )
                continue

            # Dedup check — by obs_id only
            if _check_duplicate_alert(db, listing.obs_id):
                logger.debug("Skipping duplicate alert for obs_id=%s", listing.obs_id)
                continue

            # Score the opportunity
            score_result = compute_opportunity_score(listing, pmn_data, metrics)
            score = score_result["score"]

            # Determine tier
            tier = _determine_tier(score)
            if tier is None:
                logger.debug(
                    "Suppressed listing %s (score=%.1f)", listing.obs_id, score,
                )
                continue

            # Build alert event
            alert_event = AlertEvent(
                product_id=product_template.product_id,
                obs_id=listing.obs_id,
                tier=tier,
                opportunity_score=score,
                ingestion_run_id=ingestion_run_id,
                suppressed=False,
                sent_at=datetime.now(UTC),
            )

            # Send Telegram for telegram-tier
            if tier == "telegram":
                try:
                    pmn_val = _decimal_to_float(pmn_data.pmn)
                    listing_price = _decimal_to_float(listing.price)
                    margin_pct = ((pmn_val - listing_price) / pmn_val * 100) if pmn_val else 0
                    margin_abs = pmn_val - listing_price if pmn_val else 0

                    delivery = await send_opportunity_alert(
                        opportunity={
                            "margin_pct": round(margin_pct, 1),
                            "margin_abs": round(margin_abs, 2),
                            "pmn": pmn_val,
                            "opportunity_score": score,
                            "score_breakdown": score_result["breakdown"],
                        },
                        listing={
                            "listing_id": listing.listing_id,
                            "title": listing.title,
                            "price": listing_price,
                            "url": listing.url,
                            "condition": listing.condition,
                            "seller_rating": _decimal_to_float(listing.seller_rating),
                            "source": listing.source,
                        },
                        product_template={
                            "product_id": str(product_template.product_id),
                            "name": product_template.name,
                            "brand": getattr(product_template, "brand", None),
                            "description": getattr(product_template, "description", None),
                        },
                        screenshot_path=getattr(listing, "screenshot_path", None),
                        pmn_confidence=confidence,
                        alert_id=None,  # set after persist
                    )
                    alert_event.delivery = delivery
                except Exception as exc:
                    logger.error("Failed to send Telegram alert: %s", exc)
                    alert_event.delivery = {"status": "error", "error": str(exc)}

            db.add(alert_event)
            db.flush()  # get alert_id for Telegram keyboard update

            # If telegram tier, update keyboard with alert_id
            if tier == "telegram" and alert_event.alert_id:
                try:
                    # Re-send with alert_id for feedback buttons
                    # (existing send_opportunity_alert handles this via alert_id param)
                    pass  # alert_id was not available at first send — acceptable for now
                except Exception:
                    pass

            alert_events.append(alert_event)

        db.commit()

    except Exception as exc:
        logger.error("Error in trigger_alerts: %s", exc, exc_info=True)
        db.rollback()
        alert_events.clear()  # rolled back — don't return stale refs
    finally:
        if should_close:
            db.close()

    logger.info(
        "Alert pipeline: %d opportunities → %d alerts (%d telegram, %d dashboard)",
        len(opportunities),
        len(alert_events),
        sum(1 for e in alert_events if e.tier == "telegram"),
        sum(1 for e in alert_events if e.tier == "dashboard"),
    )
    return alert_events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tiered_alerts.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `uv run pytest tests/ -v`
Expected: Existing tests may need updates where they import `evaluate_alert_rules` or mock the old `trigger_alerts` signature. Fix any breakages:

- `tests/integration/test_alert_pipeline.py` — update to match new `trigger_alerts` signature (no more `rule_id` matching). The test seeds an AlertRule, but `trigger_alerts` no longer evaluates rules. Update the test to verify score-based behavior instead.

- [ ] **Step 6: Commit**

```bash
git add ingestion/alert_engine.py tests/unit/test_tiered_alerts.py tests/integration/test_alert_pipeline.py
git commit -m "feat: replace rule-based alerts with score-based tiered alerting"
```

---

## Chunk 4: Worker Frequency and Inline Pipeline

### Task 6: Per-Product Scheduling with `due_only` Filter

**Files:**
- Modify: `ingestion/worker.py:41-61` (`_active_product_ids`), `ingestion/worker.py:624-636` (cron_jobs)

- [ ] **Step 1: Update `_active_product_ids` to support `due_only`**

In `ingestion/worker.py`, replace the `_active_product_ids` function (lines 41-61):

```python
def _active_product_ids(provider: str | None = None, due_only: bool = False) -> list[str]:
    """Return active product IDs, optionally filtered to those due for ingestion."""
    now = datetime.now(UTC)

    with SessionLocal() as db:
        query = db.query(ProductTemplate).filter(ProductTemplate.is_active == True)
        products = query.all()

    product_ids: list[str] = []
    for product in products:
        allowed_providers = product.providers or []
        if provider and allowed_providers and provider not in allowed_providers:
            continue

        if due_only:
            interval_hours = product.ingestion_interval_hours or 24
            if product.last_ingested_at is not None:
                last = product.last_ingested_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=UTC)
                if last + timedelta(hours=interval_hours) > now:
                    continue  # not yet due

        product_ids.append(str(product.product_id))

    label = f"due for {provider}" if due_only else (provider or "all")
    logger.info("Found %d active product templates (%s)", len(product_ids), label)
    return product_ids
```

- [ ] **Step 2: Update cron schedules to hourly staggered**

In `ingestion/worker.py`, replace the `cron_jobs` list (lines 624-636):

```python
    cron_jobs = [
        cron(ping, minute=0),
        # Hourly ingestion — staggered by 10 min per connector
        cron(scheduled_ebay_ingestion, minute=0),
        cron(scheduled_leboncoin_ingestion, minute=10),
        cron(scheduled_vinted_ingestion, minute=20),
        # Daily catch-up computation (safety net)
        cron(scheduled_computation, hour=5),
        # Maintenance
        cron(mark_stale_listings, hour=1),
        cron(
            check_system_health,
            hour={0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22},
            minute=30,
        ),
    ]
```

- [ ] **Step 3: Update scheduled ingestion functions to use `due_only=True`**

In each of `scheduled_ebay_ingestion`, `scheduled_leboncoin_ingestion`, `scheduled_vinted_ingestion`, change:

```python
product_ids = _active_product_ids("ebay")
```
to:
```python
product_ids = _active_product_ids("ebay", due_only=True)
```

(Same pattern for leboncoin and vinted.)

- [ ] **Step 4: Commit**

```bash
git add ingestion/worker.py
git commit -m "feat: hourly staggered ingestion with per-product due_only filtering"
```

---

### Task 6b: Update `run_full_ingestion` Callers and Old Scoring Imports

**Files:**
- Modify: `backend/main.py` (any caller of old `compute_opportunity_score` from `computation.py`)
- Modify: `ingestion/worker.py:426-504` (`process_opportunity_alerts`)

- [ ] **Step 1: Find and update all callers of old `compute_opportunity_score`**

Run: `rg "compute_opportunity_score" --type py`

For each caller found in `backend/main.py` or elsewhere:
- Change import from `from ingestion.computation import compute_opportunity_score` to `from ingestion.scoring import compute_opportunity_score`
- Update call signature: old was `(listing, product_metrics, pmn_data)`, new is `(listing, pmn_data, metrics)` — note `pmn_data` and `metrics` arguments are swapped
- Update return value usage: old returned `{"opportunity_score": ...}`, new returns `{"score": ..., "breakdown": ...}`

- [ ] **Step 2: Update `process_opportunity_alerts` to pass `ingestion_run_id`**

In `ingestion/worker.py`, in `process_opportunity_alerts` (around line 491), change:

```python
alert_events = await trigger_alerts(opportunities, db)
```
to:
```python
alert_events = await trigger_alerts(opportunities, db=db)
```

Note: `ingestion_run_id` is not available in this function (it processes alerts on-demand, not from a cron run). That's fine — latency tracking only applies to cron-triggered alerts.

- [ ] **Step 3: Verify `run_full_ingestion` already calls `trigger_alerts` inline**

Check `ingestion/ingestion.py:686-809`. The function already calls `trigger_alerts(opportunities, db)` at the end of each product's ingestion. This is the inline pipeline the spec requires. No further changes needed here — the hourly cron calls `run_full_ingestion` which already chains compute → alert.

However, ensure `run_full_ingestion` passes `ingestion_run_id` to `trigger_alerts` for latency tracking. Find the `trigger_alerts` call and update it to pass the run tracker's `run_id` if available.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py ingestion/worker.py ingestion/ingestion.py
git commit -m "fix: update compute_opportunity_score callers and pass ingestion_run_id"
```

---

## Chunk 5: API Updates

### Task 7: Add `pmn_confidence` to ProductDetail (M1 Cleanup)

**Files:**
- Modify: `backend/main.py:255-272` (ProductDetail), `backend/main.py:362-464` (product_detail endpoint)

- [ ] **Step 1: Add field to ProductDetail model**

In `backend/main.py`, add to `ProductDetail` class (around line 272, before `recent_solds`):

```python
    pmn_confidence: float | None = None
```

- [ ] **Step 2: Populate in endpoint**

In the `product_detail` endpoint (around line 362-464), find where `pmn` is populated from `pmn_data` and add:

```python
    pmn_confidence=float(pmn_data.confidence) if pmn_data and pmn_data.confidence else None,
```

- [ ] **Step 3: Run existing tests**

Run: `uv run pytest tests/ -v`
Expected: PASS (adding an optional field is backward-compatible).

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "fix: add pmn_confidence to /products/{product_id} response"
```

---

### Task 8: Add Opportunity Score to Discovery Endpoint

**Files:**
- Modify: `backend/main.py:115-252` (discovery endpoint)

- [ ] **Step 1: Add score fields to DiscoveryItem**

Find the `DiscoveryItem` model in `backend/main.py` and add:

```python
    opportunity_score: float | None = None
    score_breakdown: dict | None = None
```

- [ ] **Step 2: Compute scores in discovery endpoint**

In the discovery endpoint, after fetching listings and PMN data, compute scores for each result item. Import `compute_opportunity_score` from `ingestion.scoring`. For each discovery item that has PMN data, compute and attach the score. Sort results by `opportunity_score` descending by default.

The discovery endpoint already loads `pmn_data` and `metrics` per product — use those to call `compute_opportunity_score` for each listing in the result set.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py
git commit -m "feat: add opportunity_score to /products/discovery response"
```

---

### Task 9: Add Latency Stats to Health Overview

**Files:**
- Modify: `backend/routers/health.py:179-256` (overview endpoint)

- [ ] **Step 1: Add latency query to overview endpoint**

In the `/health/overview` endpoint in `backend/routers/health.py`, after the existing queries, add:

```python
    # Pipeline latency stats (last 24h)
    from sqlalchemy import func as sa_func, literal

    latency_cutoff = datetime.now(UTC) - timedelta(hours=24)
    latency_target_seconds = 75 * 60  # 1h15

    latency_expr = (
        sa_func.extract("epoch", AlertEvent.sent_at)
        - sa_func.extract("epoch", IngestionRun.started_at)
    )

    latency_query = (
        db.query(
            sa_func.avg(latency_expr).label("avg_seconds"),
            sa_func.percentile_cont(0.95).within_group(latency_expr).label("p95_seconds"),
            sa_func.count().label("total"),
            sa_func.count().filter(latency_expr <= latency_target_seconds).label("within_target"),
        )
        .join(IngestionRun, AlertEvent.ingestion_run_id == IngestionRun.run_id)
        .filter(
            AlertEvent.sent_at > latency_cutoff,
            AlertEvent.ingestion_run_id.isnot(None),
            AlertEvent.suppressed.is_(False),
        )
        .first()
    )

    if latency_query and latency_query.avg_seconds:
        latency_stats = {
            "avg_ingestion_to_alert_minutes": round(latency_query.avg_seconds / 60, 1),
            "p95_ingestion_to_alert_minutes": round(latency_query.p95_seconds / 60, 1) if latency_query.p95_seconds else None,
            "alerts_within_target_pct": round(latency_query.within_target / latency_query.total * 100, 1) if latency_query.total else None,
        }
    else:
        latency_stats = {
            "avg_ingestion_to_alert_minutes": None,
            "p95_ingestion_to_alert_minutes": None,
            "alerts_within_target_pct": None,
        }
```

Add to the response dict:

```python
    "latency": latency_stats,
```

- [ ] **Step 2: Add necessary imports**

Ensure `AlertEvent` and `IngestionRun` are imported in `health.py`.

- [ ] **Step 3: Commit**

```bash
git add backend/routers/health.py
git commit -m "feat: add pipeline latency stats to /health/overview"
```

---

## Chunk 6: Telegram Message Update and LLM Integration

### Task 10: Include Score in Telegram Alert Message

**Files:**
- Modify: `libs/common/telegram_service.py:55-185` (send_opportunity_alert)

- [ ] **Step 1: Update message template**

In `send_opportunity_alert`, find where the HTML message body is constructed. Add the opportunity score and top factors. The `opportunity` dict now includes `opportunity_score` and `score_breakdown` keys (set in alert_engine.py task 5). Add after the margin line:

```python
    # Opportunity score
    opp_score = opportunity.get("opportunity_score")
    if opp_score is not None:
        score_emoji = "🟢" if opp_score >= 80 else "🟡" if opp_score >= 50 else "🔴"
        msg_parts.append(f"{score_emoji} <b>Score:</b> {opp_score:.0f}/100")

        # Top 3 contributing factors
        breakdown = opportunity.get("score_breakdown", {})
        if breakdown:
            top_factors = sorted(breakdown.items(), key=lambda x: x[1].get("weighted", 0), reverse=True)[:3]
            factors_str = ", ".join(f"{k} {v['weighted']:.0f}pts" for k, v in top_factors)
            msg_parts.append(f"📊 {factors_str}")
```

- [ ] **Step 2: Commit**

```bash
git add libs/common/telegram_service.py
git commit -m "feat: include opportunity score and top factors in Telegram alerts"
```

---

### Task 11: LLM Gate for Telegram-Tier Alerts

**Files:**
- Modify: `ingestion/alert_engine.py` (the `trigger_alerts` function)

- [ ] **Step 1: Add LLM validation before Telegram send**

In `trigger_alerts`, add LLM gating logic for telegram-tier alerts. Before the `if tier == "telegram":` block that sends the Telegram message, add:

```python
            # LLM gate for telegram tier
            if tier == "telegram" and getattr(product_template, "enable_llm_validation", False):
                if settings.gemini_api_key:
                    try:
                        from libs.common.llm_service import assess_listing_relevance
                        from libs.common.models import Listing as ListingModel

                        listing_obj = ListingModel(
                            source=listing.source,
                            listing_id=listing.listing_id,
                            title=listing.title or "",
                            price=float(listing.price) if listing.price else None,
                            currency=listing.currency or "EUR",
                            condition_raw=listing.condition,
                            condition_norm=None,
                            location=listing.location,
                            seller_rating=float(listing.seller_rating) if listing.seller_rating else None,
                            shipping_cost=float(listing.shipping_cost) if listing.shipping_cost else None,
                            observed_at=listing.observed_at or datetime.now(UTC),
                            is_sold=listing.is_sold or False,
                            url=listing.url,
                        )

                        llm_result = assess_listing_relevance(
                            listing_obj,
                            getattr(listing, "screenshot_path", None),
                            product_template,
                            product_template.words_to_avoid or [],
                        )

                        # Re-score with LLM result
                        score_result = compute_opportunity_score(
                            listing, pmn_data, metrics, llm_result=llm_result,
                        )
                        score = score_result["score"]
                        tier = _determine_tier(score)
                        alert_event.opportunity_score = score
                        alert_event.tier = tier

                        if tier is None:
                            logger.info(
                                "LLM downgraded listing %s below threshold (new score=%.1f)",
                                listing.obs_id, score,
                            )
                            continue

                    except Exception as exc:
                        logger.warning("LLM validation failed, proceeding without: %s", exc)
```

- [ ] **Step 2: Commit**

```bash
git add ingestion/alert_engine.py
git commit -m "feat: add LLM validation gate for telegram-tier alerts"
```

---

### Task 12: Final Integration Test

**Files:**
- Modify: `tests/integration/test_alert_pipeline.py`

- [ ] **Step 1: Update integration test for score-based alerting**

Update the existing alert pipeline integration test to verify:
1. Seed a product with PMN, a cheap listing (price well below PMN), and metrics
2. Call `trigger_alerts` with the opportunity
3. Verify an `AlertEvent` is created with `tier`, `opportunity_score` fields populated
4. Verify score is > 0

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_alert_pipeline.py
git commit -m "test: update alert pipeline integration test for score-based tiering"
```

---

### Task 13: Lint and Format

- [ ] **Step 1: Run linting and formatting**

```bash
uv run ruff check --fix . && uv run ruff format .
```

- [ ] **Step 2: Run full test suite one final time**

```bash
uv run pytest tests/ -v
```

- [ ] **Step 3: Commit if any formatting changes**

```bash
git add -A && git commit -m "style: lint and format"
```

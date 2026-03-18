# Connector Data Quality Audit — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an LLM-as-judge system that verifies marketplace connector extraction accuracy against real pages, with continuous sampling, on-demand API, and CLI full audit modes.

**Architecture:** New `ingestion/audit.py` module handles page capture (Playwright batch), LLM judge calls (Gemini vision + HTML), and accuracy computation. Three entry points: ARQ task for continuous sampling, FastAPI router for on-demand, CLI script for full audit with Markdown reports. Results stored in `connector_audit` table, surfaced in health dashboard and Telegram alerts.

**Tech Stack:** Python 3.11, Playwright (stealth patches from `libs/common/scraping.py`), Gemini Flash (vision + text), SQLAlchemy 2.0, ARQ, FastAPI, pytest

**Spec:** `docs/superpowers/specs/2026-03-17-connector-data-quality-audit-design.md`

---

## Chunk 1: Foundation — Migration, Model, Settings

### Task 1: Alembic Migration

**Files:**
- Create: `migrations/versions/0006_connector_audit.py`

- [ ] **Step 1: Create migration file**

```python
"""connector_audit table"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_audit",
        sa.Column("audit_id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ingestion_run_id", UUID, nullable=True),
        sa.Column("obs_id", sa.BigInteger, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("audit_mode", sa.Text, nullable=False),
        sa.Column("screenshot_path", sa.Text, nullable=True),
        sa.Column("html_snippet", sa.Text, nullable=True),
        sa.Column("llm_response", JSONB, nullable=True),
        sa.Column("field_results", JSONB, nullable=True),
        sa.Column("accuracy_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("audited_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("cost_tokens", sa.Integer, nullable=True),
    )
    op.create_foreign_key("fk_connector_audit_run", "connector_audit", "ingestion_run", ["ingestion_run_id"], ["run_id"])
    op.create_foreign_key("fk_connector_audit_obs", "connector_audit", "listing_observation", ["obs_id"], ["obs_id"])
    op.create_check_constraint("ck_connector_audit_mode", "connector_audit", "audit_mode IN ('continuous', 'on_demand', 'cli')")
    op.create_index("ix_connector_audit_source_date", "connector_audit", ["source", "audited_at"])
    op.create_index("ix_connector_audit_obs", "connector_audit", ["obs_id"])


def downgrade() -> None:
    op.drop_index("ix_connector_audit_obs")
    op.drop_index("ix_connector_audit_source_date")
    op.drop_constraint("ck_connector_audit_mode", "connector_audit", type_="check")
    op.drop_constraint("fk_connector_audit_obs", "connector_audit", type_="foreignkey")
    op.drop_constraint("fk_connector_audit_run", "connector_audit", type_="foreignkey")
    op.drop_table("connector_audit")
```

- [ ] **Step 2: Apply migration**

Run: `uv run alembic upgrade head`
Expected: Migration applies cleanly.

- [ ] **Step 3: Commit**

```bash
git add migrations/versions/0006_connector_audit.py
git commit -m "feat: add migration 0006 — connector_audit table"
```

---

### Task 2: SQLAlchemy Model

**Files:**
- Modify: `libs/common/models.py`

- [ ] **Step 1: Add JSONB import**

In `libs/common/models.py`, the imports at the top (line 1-5) use `JSON`. Add `JSONB` import from PostgreSQL dialect:

```python
from sqlalchemy.dialects.postgresql import JSONB
```

Note: If `JSONB` is not already imported, add it. The existing code uses `JSON` for other columns; `JSONB` is needed for queryable JSON columns.

- [ ] **Step 2: Add ConnectorAudit model**

Add after the `AlertFeedback` model (after line 214):

```python
class ConnectorAudit(Base):
    __tablename__ = "connector_audit"
    __table_args__ = (
        CheckConstraint(
            "audit_mode IN ('continuous', 'on_demand', 'cli')",
            name="ck_connector_audit_mode",
        ),
    )

    audit_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    ingestion_run_id = Column(UUID, ForeignKey("ingestion_run.run_id"))
    obs_id = Column(BigInteger, ForeignKey("listing_observation.obs_id"), nullable=False)
    source = Column(Text, nullable=False)
    audit_mode = Column(Text, nullable=False)
    screenshot_path = Column(Text)
    html_snippet = Column(Text)
    llm_response = Column(JSONB)
    field_results = Column(JSONB)
    accuracy_score = Column(Numeric(3, 2))
    audited_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    cost_tokens = Column(Integer)

    observation = relationship("ListingObservation")
    ingestion_run = relationship("IngestionRun")
```

- [ ] **Step 3: Commit**

```bash
git add libs/common/models.py
git commit -m "feat: add ConnectorAudit SQLAlchemy model"
```

---

### Task 3: Settings

**Files:**
- Modify: `libs/common/settings.py`

- [ ] **Step 1: Add audit settings**

Add after the observability settings block (after line 44 `min_pmn_confidence`):

```python
    # Connector audit
    audit_enabled: bool = True
    audit_sample_size: int = 3
    audit_accuracy_green: float = 0.90
    audit_accuracy_yellow: float = 0.80
    audit_daily_token_budget: int = 100000
```

- [ ] **Step 2: Commit**

```bash
git add libs/common/settings.py
git commit -m "feat: add connector audit settings"
```

---

## Chunk 2: Core Audit Logic (TDD)

### Task 4: Audit Module — Tests First

**Files:**
- Create: `tests/unit/test_audit.py`
- Create: `ingestion/audit.py`

- [ ] **Step 1: Write tests for verdict parsing and accuracy computation**

Create `tests/unit/test_audit.py`:

```python
"""Tests for connector data quality audit logic."""

import pytest

from ingestion.audit import (
    compute_accuracy,
    detect_antibot,
    parse_llm_verdict,
)


class TestParseVerdict:
    def test_valid_json_response(self):
        raw = {
            "fields": {
                "price": {"verdict": "correct"},
                "title": {"verdict": "correct"},
                "condition": {"verdict": "incorrect", "expected": "Bon état", "extracted": None},
            },
            "overall": "partial_match",
            "notes": "Condition missing",
        }
        result = parse_llm_verdict(raw)

        assert result["price"]["verdict"] == "correct"
        assert result["condition"]["verdict"] == "incorrect"
        assert len(result) == 3

    def test_missing_fields_key(self):
        raw = {"notes": "malformed"}
        result = parse_llm_verdict(raw)

        assert result == {}

    def test_invalid_verdict_value_treated_as_unverifiable(self):
        raw = {
            "fields": {
                "price": {"verdict": "maybe"},
            }
        }
        result = parse_llm_verdict(raw)

        assert result["price"]["verdict"] == "unverifiable"


class TestComputeAccuracy:
    def test_all_correct(self):
        fields = {
            "price": {"verdict": "correct"},
            "title": {"verdict": "correct"},
            "condition": {"verdict": "correct"},
        }
        assert compute_accuracy(fields) == 1.0

    def test_one_incorrect(self):
        fields = {
            "price": {"verdict": "correct"},
            "title": {"verdict": "incorrect"},
        }
        assert compute_accuracy(fields) == 0.5

    def test_unverifiable_excluded(self):
        """Accuracy computed over verifiable fields only."""
        fields = {
            "price": {"verdict": "correct"},
            "title": {"verdict": "correct"},
            "shipping_cost": {"verdict": "unverifiable"},
        }
        assert compute_accuracy(fields) == 1.0

    def test_all_unverifiable_returns_none(self):
        fields = {
            "price": {"verdict": "unverifiable"},
        }
        assert compute_accuracy(fields) is None

    def test_empty_fields(self):
        assert compute_accuracy({}) is None


class TestDetectAntibot:
    def test_captcha_detected(self):
        html = "<html><body><div class='captcha'>Please verify you are human</div></body></html>"
        assert detect_antibot(html) is True

    def test_login_wall_detected(self):
        html = "<html><body><form>Connectez-vous pour continuer</form></body></html>"
        assert detect_antibot(html) is True

    def test_normal_page(self):
        html = "<html><body><h1>iPhone 14 Pro</h1><span>85 €</span></body></html>"
        assert detect_antibot(html) is False

    def test_robot_check(self):
        html = "<html><body>Are you a robot?</body></html>"
        assert detect_antibot(html) is True

    def test_empty_html(self):
        assert detect_antibot("") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingestion.audit'`

- [ ] **Step 3: Implement core audit functions**

Create `ingestion/audit.py`:

```python
"""Connector data quality audit — LLM-as-judge for extraction verification."""

from __future__ import annotations

import base64
import html as html_lib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from libs.common.models import ConnectorAudit, ListingObservation
from libs.common.settings import settings

VALID_VERDICTS = {"correct", "incorrect", "unverifiable"}

AUDITED_FIELDS = [
    "price", "title", "condition", "is_sold",
    "location", "seller_rating", "shipping_cost",
]

ANTIBOT_PATTERNS = re.compile(
    r"captcha|verify you are human|are you a robot|"
    r"connectez-vous pour continuer|veuillez vous connecter|"
    r"access denied|blocked|cloudflare|challenge-platform",
    re.IGNORECASE,
)


@dataclass
class AuditCapture:
    screenshot_path: str | None
    html_snippet: str | None


def parse_llm_verdict(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract per-field verdicts from LLM response. Returns {field: {verdict, ...}}."""
    fields = raw.get("fields")
    if not fields or not isinstance(fields, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for field_name, field_data in fields.items():
        if not isinstance(field_data, dict):
            continue
        verdict = field_data.get("verdict", "unverifiable")
        if verdict not in VALID_VERDICTS:
            verdict = "unverifiable"
        result[field_name] = {**field_data, "verdict": verdict}
    return result


def compute_accuracy(field_results: dict[str, dict[str, Any]]) -> float | None:
    """Compute accuracy over verifiable fields. Returns None if no verifiable fields."""
    verifiable = [
        f for f in field_results.values()
        if f.get("verdict") in ("correct", "incorrect")
    ]
    if not verifiable:
        return None
    correct = sum(1 for f in verifiable if f["verdict"] == "correct")
    return round(correct / len(verifiable), 2)


def detect_antibot(html: str) -> bool:
    """Check if HTML contains CAPTCHA or login wall indicators."""
    if not html:
        return False
    return bool(ANTIBOT_PATTERNS.search(html))


def _build_extracted_fields(listing: ListingObservation) -> dict[str, Any]:
    """Build the extracted fields dict to send to the LLM judge."""
    return {
        "title": listing.title,
        "price": float(listing.price) if listing.price else None,
        "currency": listing.currency if hasattr(listing, "currency") else "EUR",
        "condition": listing.condition,
        "location": listing.location,
        "seller_rating": float(listing.seller_rating) if listing.seller_rating else None,
        "shipping_cost": float(listing.shipping_cost) if listing.shipping_cost else None,
        "is_sold": listing.is_sold,
    }


def _build_judge_prompt(extracted: dict[str, Any], has_screenshot: bool) -> str:
    """Build the LLM judge system prompt."""
    return f"""You are a data quality auditor for a marketplace scraping system.

You will be given:
{"- A screenshot of a marketplace listing page" if has_screenshot else ""}
- The raw HTML of the listing page
- Fields extracted by our scraper

Your task: for each extracted field, compare it against what you see on the page.

Return a JSON object with this exact structure:
{{
  "fields": {{
    "price": {{"verdict": "correct|incorrect|unverifiable", "expected": "value from page", "extracted": "value from scraper"}},
    "title": {{"verdict": "...", ...}},
    "condition": {{"verdict": "...", ...}},
    "is_sold": {{"verdict": "...", ...}},
    "location": {{"verdict": "...", ...}},
    "seller_rating": {{"verdict": "...", ...}},
    "shipping_cost": {{"verdict": "...", ...}}
  }},
  "overall": "correct|partial_match|incorrect",
  "notes": "Any observations about extraction quality"
}}

Rules:
- "correct" = extracted value matches page content (minor formatting differences OK, e.g. "85.0" vs "85,00 €")
- "incorrect" = extracted value clearly wrong or missing when visible on page
- "unverifiable" = field not visible on page or requires interaction to see
- For price: currency must also match. Shipping cost included in price = incorrect.
- For condition: match the marketplace's condition label, not your interpretation
- For is_sold: look for "sold" badges, crossed-out prices, or "vendu" labels

Extracted fields:
{json.dumps(extracted, ensure_ascii=False, indent=2)}

Return ONLY valid JSON, no markdown fences."""


async def judge_listing(
    listing: ListingObservation,
    capture: AuditCapture,
) -> dict[str, Any]:
    """
    Run LLM judge on a single listing.

    Returns dict with keys: field_results, accuracy_score, llm_response, cost_tokens.
    """
    extracted = _build_extracted_fields(listing)

    # Check for antibot before calling LLM
    if capture.html_snippet and detect_antibot(capture.html_snippet):
        blocked_results = {
            f: {"verdict": "unverifiable", "reason": "blocked_by_antibot"}
            for f in AUDITED_FIELDS
        }
        return {
            "field_results": blocked_results,
            "accuracy_score": None,
            "llm_response": {"blocked": True, "reason": "antibot_detected"},
            "cost_tokens": 0,
        }

    has_screenshot = capture.screenshot_path is not None
    prompt = _build_judge_prompt(extracted, has_screenshot)

    # Build message parts
    parts: list[dict[str, Any]] = []

    # Screenshot (vision input)
    if capture.screenshot_path and os.path.exists(capture.screenshot_path):
        with open(capture.screenshot_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
        })

    # HTML snippet (text input)
    if capture.html_snippet:
        # Truncate to ~50KB to avoid token explosion
        snippet = capture.html_snippet[:50000]
        parts.append({
            "type": "text",
            "text": f"Raw HTML of the listing page:\n\n{snippet}",
        })

    parts.append({"type": "text", "text": prompt})

    # Call Gemini
    try:
        import google.generativeai as genai

        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not set")

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)

        # Build content for Gemini API
        content_parts = []
        for part in parts:
            if part["type"] == "image_url":
                # Decode base64 back to bytes for Gemini
                img_bytes = base64.b64decode(part["image_url"]["url"].split(",")[1])
                content_parts.append({"mime_type": "image/png", "data": img_bytes})
            else:
                content_parts.append(part["text"])

        response = model.generate_content(
            content_parts,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )

        # Parse response
        raw_text = response.text.strip()
        # Strip markdown fences if present
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        llm_response = json.loads(raw_text)
        cost_tokens = response.usage_metadata.total_token_count if hasattr(response, "usage_metadata") else 0

    except Exception as exc:
        logger.error("LLM judge call failed for obs_id=%s: %s", listing.obs_id, exc)
        error_results = {
            f: {"verdict": "unverifiable", "reason": f"llm_error: {exc}"}
            for f in AUDITED_FIELDS
        }
        return {
            "field_results": error_results,
            "accuracy_score": None,
            "llm_response": {"error": str(exc)},
            "cost_tokens": 0,
        }

    field_results = parse_llm_verdict(llm_response)
    accuracy = compute_accuracy(field_results)

    return {
        "field_results": field_results,
        "accuracy_score": accuracy,
        "llm_response": llm_response,
        "cost_tokens": cost_tokens,
    }


async def capture_audit_batch(
    listings: list[ListingObservation],
    html_only: bool = False,
) -> dict[int, AuditCapture]:
    """
    Capture screenshot + HTML for a batch of listings using one Playwright browser.

    Args:
        listings: list of ListingObservation with URLs
        html_only: skip screenshots, capture HTML only

    Returns:
        {obs_id: AuditCapture(screenshot_path, html_snippet)}
    """
    import asyncio
    import random

    results: dict[int, AuditCapture] = {}

    listings_with_urls = [l for l in listings if l.url]
    if not listings_with_urls:
        return results

    try:
        from playwright.async_api import async_playwright

        from libs.common.scraping import STEALTH_PATCH

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--webrtc-ip-handling-policy=disable_non_proxied_udp"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="fr-FR",
                timezone_id="Europe/Paris",
            )
            await context.add_init_script(STEALTH_PATCH)

            page = await context.new_page()

            for listing in listings_with_urls:
                try:
                    await page.goto(listing.url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)  # let dynamic content load

                    # Capture HTML
                    html_content = await page.content()
                    html_snippet = html_content[:50000] if html_content else None

                    # Capture screenshot (unless html_only)
                    screenshot_path = None
                    if not html_only:
                        screenshots_dir = Path(settings.screenshot_storage_path) / "audit"
                        screenshots_dir.mkdir(parents=True, exist_ok=True)
                        screenshot_file = screenshots_dir / f"audit_{listing.obs_id}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.png"
                        await page.screenshot(
                            path=str(screenshot_file),
                            full_page=True,
                            clip={"x": 0, "y": 0, "width": 1920, "height": 3000},
                        )
                        screenshot_path = str(screenshot_file)

                    results[listing.obs_id] = AuditCapture(
                        screenshot_path=screenshot_path,
                        html_snippet=html_snippet,
                    )

                except Exception as exc:
                    logger.warning("Failed to capture listing %s (%s): %s", listing.obs_id, listing.url, exc)
                    results[listing.obs_id] = AuditCapture(screenshot_path=None, html_snippet=None)

                # Anti-bot delay: 2-3 seconds between pages
                await asyncio.sleep(2 + random.random())

            await browser.close()

    except ImportError:
        logger.error("Playwright not installed — cannot capture audit pages")
    except Exception as exc:
        logger.error("Batch capture failed: %s", exc, exc_info=True)

    return results


async def audit_listings(
    listings: list[ListingObservation],
    audit_mode: str,
    ingestion_run_id: str | None = None,
    html_only: bool = False,
) -> list[ConnectorAudit]:
    """
    Full audit pipeline: capture pages → judge each → store results.

    Args:
        listings: listings to audit (must have URLs)
        audit_mode: 'continuous', 'on_demand', or 'cli'
        ingestion_run_id: optional FK for latency tracking
        html_only: skip screenshots

    Returns:
        list of ConnectorAudit records (not yet committed to DB)
    """
    from libs.common.db import SessionLocal

    # Check daily token budget for continuous mode
    if audit_mode == "continuous":
        with SessionLocal() as db:
            today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            from sqlalchemy import func as sa_func
            used_tokens = (
                db.query(sa_func.coalesce(sa_func.sum(ConnectorAudit.cost_tokens), 0))
                .filter(ConnectorAudit.audited_at >= today_start)
                .scalar()
            )
            if used_tokens >= settings.audit_daily_token_budget:
                logger.warning(
                    "Daily audit token budget exhausted (%d/%d). Skipping continuous audit.",
                    used_tokens, settings.audit_daily_token_budget,
                )
                return []

    # Capture pages
    captures = await capture_audit_batch(listings, html_only=html_only)

    # Judge each listing
    audit_records: list[ConnectorAudit] = []
    for listing in listings:
        capture = captures.get(listing.obs_id)
        if not capture:
            continue

        # Skip if no content captured at all
        if not capture.screenshot_path and not capture.html_snippet:
            logger.warning("No content captured for obs_id=%s, skipping", listing.obs_id)
            continue

        result = await judge_listing(listing, capture)

        record = ConnectorAudit(
            ingestion_run_id=ingestion_run_id,
            obs_id=listing.obs_id,
            source=listing.source,
            audit_mode=audit_mode,
            screenshot_path=capture.screenshot_path,
            html_snippet=capture.html_snippet[:1000] if capture.html_snippet else None,  # store truncated for DB
            llm_response=result["llm_response"],
            field_results=result["field_results"],
            accuracy_score=result["accuracy_score"],
            audited_at=datetime.now(UTC),
            cost_tokens=result["cost_tokens"],
        )
        audit_records.append(record)

    return audit_records


def compute_connector_accuracy(
    audit_records: list[ConnectorAudit],
) -> dict[str, dict[str, Any]]:
    """
    Aggregate accuracy stats per connector from audit records.

    Returns: {source: {accuracy, sample_size, per_field: {field: accuracy}, status}}
    """
    from collections import defaultdict

    by_source: dict[str, list[ConnectorAudit]] = defaultdict(list)
    for r in audit_records:
        by_source[r.source].append(r)

    result: dict[str, dict[str, Any]] = {}
    for source, records in by_source.items():
        scores = [r.accuracy_score for r in records if r.accuracy_score is not None]
        avg_accuracy = sum(scores) / len(scores) if scores else None

        # Per-field accuracy
        field_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "incorrect": 0, "unverifiable": 0})
        for r in records:
            if not r.field_results:
                continue
            for field_name, field_data in r.field_results.items():
                verdict = field_data.get("verdict", "unverifiable")
                if verdict in field_stats[field_name]:
                    field_stats[field_name][verdict] += 1

        per_field: dict[str, float | None] = {}
        for field_name, stats in field_stats.items():
            verifiable = stats["correct"] + stats["incorrect"]
            per_field[field_name] = round(stats["correct"] / verifiable, 2) if verifiable > 0 else None

        # Status
        if avg_accuracy is None:
            status = "unknown"
        elif avg_accuracy >= settings.audit_accuracy_green:
            status = "green"
        elif avg_accuracy >= settings.audit_accuracy_yellow:
            status = "yellow"
        else:
            status = "red"

        result[source] = {
            "accuracy": round(avg_accuracy, 3) if avg_accuracy is not None else None,
            "sample_size": len(records),
            "per_field": per_field,
            "status": status,
        }

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_audit.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ingestion/audit.py tests/unit/test_audit.py
git commit -m "feat: add connector audit core — LLM judge, page capture, accuracy computation"
```

---

## Chunk 3: Worker Integration — Continuous Sampling

### Task 5: ARQ Task for Post-Ingestion Audit Sampling

**Files:**
- Modify: `ingestion/worker.py`
- Modify: `ingestion/ingestion.py:686-809` (`run_full_ingestion`)

- [ ] **Step 1: Add `audit_ingestion_sample` ARQ task**

In `ingestion/worker.py`, add a new task after the `check_system_health` function (after line 596):

```python
async def audit_ingestion_sample(ctx: dict, source: str, ingestion_run_id: str | None = None) -> dict:
    """Sample recent listings from an ingestion run and audit them."""
    if not settings.audit_enabled:
        return {"status": "disabled"}

    with SessionLocal() as db:
        # Get recent listings from this source with URLs
        query = (
            db.query(ListingObservation)
            .filter(
                ListingObservation.source == source,
                ListingObservation.url.isnot(None),
            )
            .order_by(ListingObservation.last_seen_at.desc())
            .limit(settings.audit_sample_size)
        )
        listings = query.all()

        if not listings:
            return {"status": "no_listings", "source": source}

        # Detach from session for async use
        for l in listings:
            db.expunge(l)

    from ingestion.audit import audit_listings

    records = await audit_listings(
        listings,
        audit_mode="continuous",
        ingestion_run_id=ingestion_run_id,
    )

    # Persist
    with SessionLocal() as db:
        for r in records:
            db.add(r)
        db.commit()

    # Check if any connector dropped below threshold
    from ingestion.audit import compute_connector_accuracy

    accuracy_data = compute_connector_accuracy(records)
    for src, data in accuracy_data.items():
        if data["status"] == "red":
            try:
                from libs.common.telegram_service import send_connector_quality_alert

                await send_connector_quality_alert(src, data)
            except Exception as exc:
                logger.error("Failed to send quality alert: %s", exc)

    return {
        "status": "success",
        "source": source,
        "audited": len(records),
        "accuracy": {s: d["accuracy"] for s, d in accuracy_data.items()},
    }
```

- [ ] **Step 2: Register the task in WorkerSettings**

In the `WorkerSettings.functions` list (around line 601-623), add `audit_ingestion_sample`:

```python
        # Audit tasks
        audit_ingestion_sample,
```

- [ ] **Step 3: Wire post-ingestion audit in scheduled ingestion functions**

In each of `scheduled_ebay_ingestion`, `scheduled_leboncoin_ingestion`, `scheduled_vinted_ingestion`, add after the ingestion loop completes (before the `return results` line):

```python
    # Trigger audit sampling
    if settings.audit_enabled:
        try:
            from arq.connections import ArqRedis

            pool: ArqRedis = ctx.get("redis") or ctx.get("pool")
            if pool:
                await pool.enqueue_job("audit_ingestion_sample", source="ebay")
        except Exception as exc:
            logger.warning("Failed to enqueue audit task: %s", exc)
```

(Replace `"ebay"` with `"leboncoin"` and `"vinted"` for the respective functions.)

- [ ] **Step 4: Commit**

```bash
git add ingestion/worker.py
git commit -m "feat: add continuous audit sampling as post-ingestion ARQ task"
```

---

### Task 6: Telegram Quality Alert Function

**Files:**
- Modify: `libs/common/telegram_service.py`

- [ ] **Step 1: Add `send_connector_quality_alert` function**

Add after `send_system_alert` (after line ~248):

```python
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

    # Build field breakdown
    field_lines = []
    for field, acc in sorted(per_field.items(), key=lambda x: x[1] or 0):
        if acc is None:
            continue
        icon = "✓" if acc >= 0.9 else "✗"
        field_lines.append(f"  - {field}: {acc:.0%} {icon}")

    msg = (
        f"⚠️ <b>Connector Quality Alert</b>\n\n"
        f"🔴 <b>{html_lib.escape(source)}</b>: accuracy {accuracy:.0%} (threshold {threshold:.0%})\n"
        f"\n".join(field_lines) + "\n\n"
        f"Last 7d: {sample_size} listings audited\n\n"
        f"Action: check {html_lib.escape(source)} connector for HTML structure changes"
    )

    try:
        from telegram import Bot

        bot = Bot(token=settings.telegram_bot_token)
        message = await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=msg,
            parse_mode="HTML",
        )
        return {"status": "success", "message_id": message.message_id}
    except Exception as exc:
        logger.error("Failed to send quality alert: %s", exc)
        return {"status": "error", "error": str(exc)}
```

Add `import html as html_lib` at the top of the file if not already present.

- [ ] **Step 2: Commit**

```bash
git add libs/common/telegram_service.py
git commit -m "feat: add send_connector_quality_alert Telegram function"
```

---

## Chunk 4: CLI Full Audit

### Task 7: CLI Entry Point with Report Generation

**Files:**
- Create: `ingestion/audit_cli.py`

- [ ] **Step 1: Create CLI module**

Create `ingestion/audit_cli.py`:

```python
"""CLI for running full connector data quality audits with detailed reports."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from ingestion.audit import (
    AuditCapture,
    audit_listings,
    compute_connector_accuracy,
)
from libs.common.db import SessionLocal
from libs.common.models import ConnectorAudit, ListingObservation, ProductTemplate
from libs.common.settings import settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connector data quality audit")
    parser.add_argument(
        "--connectors",
        default="ebay,leboncoin,vinted",
        help="Comma-separated connectors to audit (default: all)",
    )
    parser.add_argument(
        "--products-per-connector",
        type=int,
        default=5,
        help="Products to test per connector (default: 5)",
    )
    parser.add_argument(
        "--listings-per-product",
        type=int,
        default=20,
        help="Ingestion limit per product (default: 20)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Report output directory (default: reports/connector-audit-YYYY-MM-DD/)",
    )
    parser.add_argument(
        "--skip-ingestion",
        action="store_true",
        help="Audit existing recent listings instead of fresh ingestion",
    )
    parser.add_argument(
        "--product-ids",
        default=None,
        help="Specific product IDs, comma-separated (overrides --products-per-connector)",
    )
    parser.add_argument(
        "--html-only",
        action="store_true",
        help="Skip screenshots, judge from HTML only (much cheaper)",
    )
    return parser.parse_args()


async def _run_ingestion_for_audit(
    connectors: list[str],
    product_ids: list[str],
    listings_per_product: int,
) -> dict[str, list[ListingObservation]]:
    """Run fresh ingestion and return listings by connector."""
    from ingestion.ingestion import run_full_ingestion

    results: dict[str, list[ListingObservation]] = defaultdict(list)

    for product_id in product_ids:
        for connector in connectors:
            source_map = {
                "ebay": {"ebay_sold": listings_per_product, "ebay_listings": listings_per_product},
                "leboncoin": {"leboncoin_listings": listings_per_product, "leboncoin_sold": listings_per_product},
                "vinted": {"vinted_listings": listings_per_product},
            }
            limits = source_map.get(connector, {})
            try:
                logger.info("Ingesting %s for product %s (limit %d)", connector, product_id, listings_per_product)
                await run_full_ingestion(product_id, limits, sources=[connector])
            except Exception as exc:
                logger.error("Ingestion failed for %s/%s: %s", connector, product_id, exc)

    # Fetch recently ingested listings
    with SessionLocal() as db:
        for connector in connectors:
            listings = (
                db.query(ListingObservation)
                .filter(
                    ListingObservation.source == connector,
                    ListingObservation.url.isnot(None),
                    ListingObservation.product_id.in_(product_ids),
                )
                .order_by(ListingObservation.last_seen_at.desc())
                .limit(500)
                .all()
            )
            for l in listings:
                db.expunge(l)
            results[connector] = listings

    return results


def _get_recent_listings(connectors: list[str], limit_per_connector: int = 100) -> dict[str, list[ListingObservation]]:
    """Fetch recent listings from DB for --skip-ingestion mode."""
    results: dict[str, list[ListingObservation]] = {}
    with SessionLocal() as db:
        for connector in connectors:
            listings = (
                db.query(ListingObservation)
                .filter(
                    ListingObservation.source == connector,
                    ListingObservation.url.isnot(None),
                )
                .order_by(ListingObservation.last_seen_at.desc())
                .limit(limit_per_connector)
                .all()
            )
            for l in listings:
                db.expunge(l)
            results[connector] = listings
    return results


def _generate_connector_report(
    source: str,
    records: list[ConnectorAudit],
    accuracy_data: dict[str, Any],
) -> str:
    """Generate detailed Markdown report for a single connector."""
    accuracy = accuracy_data.get("accuracy")
    per_field = accuracy_data.get("per_field", {})
    status = accuracy_data.get("status", "unknown")
    threshold = settings.audit_accuracy_yellow

    verdict = "PASS" if status in ("green", "yellow") else "FAIL" if status == "red" else "UNKNOWN"
    verdict_icon = "✅" if verdict == "PASS" else "❌" if verdict == "FAIL" else "❓"

    lines = [
        f"# {source.title()} Connector Audit — {datetime.now(UTC).strftime('%Y-%m-%d')}",
        "",
        "## Summary",
        f"- Listings audited: {len(records)}",
        f"- Overall accuracy: {accuracy:.1%}" if accuracy is not None else "- Overall accuracy: N/A",
        f"- Verdict: {verdict_icon} {verdict} (threshold: {threshold:.0%})",
        "",
        "## Per-Field Accuracy",
        "",
        "| Field | Correct | Incorrect | Unverifiable | Accuracy |",
        "|-------|---------|-----------|--------------|----------|",
    ]

    # Aggregate per-field counts
    field_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "incorrect": 0, "unverifiable": 0})
    for r in records:
        if not r.field_results:
            continue
        for fname, fdata in r.field_results.items():
            v = fdata.get("verdict", "unverifiable")
            if v in field_counts[fname]:
                field_counts[fname][v] += 1

    for fname in sorted(field_counts.keys()):
        counts = field_counts[fname]
        verifiable = counts["correct"] + counts["incorrect"]
        acc = f"{counts['correct'] / verifiable:.1%}" if verifiable > 0 else "N/A"
        lines.append(f"| {fname} | {counts['correct']} | {counts['incorrect']} | {counts['unverifiable']} | {acc} |")

    # Failure analysis
    lines.extend(["", "## Failure Analysis", ""])
    for fname in sorted(field_counts.keys()):
        counts = field_counts[fname]
        if counts["incorrect"] == 0:
            continue
        verifiable = counts["correct"] + counts["incorrect"]
        acc = counts["correct"] / verifiable if verifiable > 0 else 0
        lines.append(f"### {fname} — {acc:.1%} accuracy")
        lines.append("")

        # Find example failures
        failures = []
        for r in records:
            if not r.field_results:
                continue
            fd = r.field_results.get(fname, {})
            if fd.get("verdict") == "incorrect":
                failures.append({
                    "obs_id": r.obs_id,
                    "expected": fd.get("expected", "?"),
                    "extracted": fd.get("extracted", "?"),
                })

        if failures:
            lines.append("| Listing | Expected | Extracted |")
            lines.append("|---------|----------|-----------|")
            for f in failures[:10]:
                lines.append(f"| {f['obs_id']} | {f['expected']} | {f['extracted']} |")
            lines.append("")

    # LLM notes
    notes = [r.llm_response.get("notes", "") for r in records if r.llm_response and r.llm_response.get("notes")]
    if notes:
        lines.extend(["## LLM Notes", ""])
        for note in set(notes):
            if note:
                lines.append(f"- {note}")
        lines.append("")

    # Raw data
    lines.extend([
        "## Raw Data",
        "",
        "<details>",
        f"<summary>Full audit results ({len(records)} listings)</summary>",
        "",
        "| obs_id | accuracy | " + " | ".join(sorted(field_counts.keys())) + " |",
        "|--------|----------|" + "|".join(["---"] * len(field_counts)) + "|",
    ])
    for r in records:
        acc_str = f"{r.accuracy_score:.0%}" if r.accuracy_score is not None else "N/A"
        field_verdicts = []
        for fname in sorted(field_counts.keys()):
            fd = (r.field_results or {}).get(fname, {})
            v = fd.get("verdict", "?")
            icon = "✓" if v == "correct" else "✗" if v == "incorrect" else "?"
            field_verdicts.append(icon)
        lines.append(f"| {r.obs_id} | {acc_str} | " + " | ".join(field_verdicts) + " |")

    lines.extend(["", "</details>", ""])

    return "\n".join(lines)


def _generate_summary_report(
    all_accuracy: dict[str, dict[str, Any]],
    all_records: dict[str, list[ConnectorAudit]],
) -> str:
    """Generate cross-connector summary report."""
    lines = [
        f"# Connector Audit Summary — {datetime.now(UTC).strftime('%Y-%m-%d')}",
        "",
        "## Overall Results",
        "",
        "| Connector | Accuracy | Listings | Status |",
        "|-----------|----------|----------|--------|",
    ]

    for source in sorted(all_accuracy.keys()):
        data = all_accuracy[source]
        acc = f"{data['accuracy']:.1%}" if data["accuracy"] is not None else "N/A"
        status_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(data["status"], "❓")
        lines.append(f"| {source} | {acc} | {data['sample_size']} | {status_icon} {data['status']} |")

    lines.extend(["", "## Per-Field Accuracy (All Connectors)", ""])

    # Merge all per-field data
    all_fields: dict[str, dict[str, float | None]] = {}
    for source, data in all_accuracy.items():
        for fname, acc in data.get("per_field", {}).items():
            if fname not in all_fields:
                all_fields[fname] = {}
            all_fields[fname][source] = acc

    if all_fields:
        headers = sorted(all_accuracy.keys())
        lines.append("| Field | " + " | ".join(headers) + " |")
        lines.append("|-------|" + "|".join(["---"] * len(headers)) + "|")
        for fname in sorted(all_fields.keys()):
            vals = []
            for h in headers:
                v = all_fields[fname].get(h)
                vals.append(f"{v:.0%}" if v is not None else "N/A")
            lines.append(f"| {fname} | " + " | ".join(vals) + " |")

    lines.append("")
    return "\n".join(lines)


async def main() -> None:
    args = _parse_args()
    connectors = [c.strip() for c in args.connectors.split(",")]

    output_dir = Path(args.output_dir or f"reports/connector-audit-{datetime.now(UTC).strftime('%Y-%m-%d')}")
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Starting connector audit: connectors=%s, output=%s", connectors, output_dir)

    # Get or ingest listings
    if args.skip_ingestion:
        logger.info("Skipping ingestion — auditing existing listings")
        listings_by_connector = _get_recent_listings(connectors)
    else:
        # Determine product IDs
        if args.product_ids:
            product_ids = [p.strip() for p in args.product_ids.split(",")]
        else:
            with SessionLocal() as db:
                products = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.is_active.is_(True))
                    .limit(args.products_per_connector)
                    .all()
                )
                product_ids = [str(p.product_id) for p in products]

        listings_by_connector = await _run_ingestion_for_audit(
            connectors, product_ids, args.listings_per_product,
        )

    # Audit each connector
    all_records: dict[str, list[ConnectorAudit]] = {}
    for connector, listings in listings_by_connector.items():
        if not listings:
            logger.warning("No listings for connector %s", connector)
            continue

        logger.info("Auditing %d listings for %s", len(listings), connector)
        records = await audit_listings(
            listings,
            audit_mode="cli",
            html_only=args.html_only,
        )

        # Persist to DB
        with SessionLocal() as db:
            for r in records:
                db.add(r)
            db.commit()

        all_records[connector] = records
        logger.info("Completed audit for %s: %d records", connector, len(records))

    # Compute accuracy
    all_flat = [r for records in all_records.values() for r in records]
    all_accuracy = compute_connector_accuracy(all_flat)

    # Generate reports
    for connector, records in all_records.items():
        if connector not in all_accuracy:
            continue
        report = _generate_connector_report(connector, records, all_accuracy[connector])
        report_path = output_dir / f"{connector}.md"
        report_path.write_text(report)
        logger.info("Report written: %s", report_path)

    summary = _generate_summary_report(all_accuracy, all_records)
    summary_path = output_dir / "summary.md"
    summary_path.write_text(summary)
    logger.info("Summary written: %s", summary_path)

    # Print summary to stdout
    print(f"\n{'='*60}")
    print(f"Audit complete. Reports in: {output_dir}")
    print(f"{'='*60}")
    for source, data in all_accuracy.items():
        acc = f"{data['accuracy']:.1%}" if data["accuracy"] is not None else "N/A"
        print(f"  {source}: {acc} ({data['status']})")
    print()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Test CLI help works**

Run: `uv run python -m ingestion.audit_cli --help`
Expected: Shows help text with all options.

- [ ] **Step 3: Commit**

```bash
git add ingestion/audit_cli.py
git commit -m "feat: add CLI full audit with Markdown report generation"
```

---

## Chunk 5: API Endpoints and Health Integration

### Task 8: Audit API Router

**Files:**
- Create: `backend/routers/audit.py`
- Modify: `backend/main.py` (add router include)

- [ ] **Step 1: Create audit router**

Create `backend/routers/audit.py`:

```python
"""Connector data quality audit API endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from libs.common.db import SessionLocal
from libs.common.models import ConnectorAudit
from libs.common.settings import settings

router = APIRouter(prefix="/audit", tags=["audit"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/connectors/results")
def get_audit_results(
    connector: str | None = None,
    days: int = 7,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Get detailed audit results with per-field accuracy breakdown."""
    cutoff = datetime.now(UTC) - timedelta(days=days)

    query = db.query(ConnectorAudit).filter(ConnectorAudit.audited_at >= cutoff)
    if connector:
        query = query.filter(ConnectorAudit.source == connector)

    records = query.order_by(ConnectorAudit.audited_at.desc()).limit(500).all()

    if not records:
        return {"results": [], "accuracy": {}}

    from ingestion.audit import compute_connector_accuracy

    accuracy = compute_connector_accuracy(records)

    # Recent failures
    failures = []
    for r in records:
        if r.accuracy_score is not None and r.accuracy_score < settings.audit_accuracy_yellow:
            failures.append({
                "obs_id": r.obs_id,
                "source": r.source,
                "accuracy": float(r.accuracy_score),
                "audited_at": r.audited_at.isoformat(),
                "notes": r.llm_response.get("notes") if r.llm_response else None,
                "field_results": r.field_results,
            })

    return {
        "period_days": days,
        "total_audited": len(records),
        "accuracy": accuracy,
        "recent_failures": failures[:20],
    }


@router.post("/connectors")
async def trigger_on_demand_audit(
    connector: str | None = None,
    sample_size: int = 20,
    product_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Trigger an on-demand audit. Returns immediately with job status."""
    import redis as redis_lib

    # Concurrency guard
    r = redis_lib.from_url(settings.redis_url)
    lock_key = "audit:on_demand:running"
    if r.exists(lock_key):
        raise HTTPException(status_code=409, detail="An on-demand audit is already running")

    # Set lock with 30 min TTL
    r.setex(lock_key, 1800, "running")

    try:
        from backend.main import enqueue_arq_job

        job = await enqueue_arq_job(
            "run_on_demand_audit",
            connector=connector,
            sample_size=sample_size,
            product_id=product_id,
        )
        return {"status": "enqueued", "job_id": str(job) if job else None}
    except Exception as exc:
        r.delete(lock_key)
        raise HTTPException(status_code=500, detail=str(exc))
```

- [ ] **Step 2: Add on-demand audit ARQ task in worker.py**

In `ingestion/worker.py`, add a new task:

```python
async def run_on_demand_audit(
    ctx: dict,
    connector: str | None = None,
    sample_size: int = 20,
    product_id: str | None = None,
) -> dict:
    """Run an on-demand connector audit."""
    import redis as redis_lib

    try:
        with SessionLocal() as db:
            query = db.query(ListingObservation).filter(
                ListingObservation.url.isnot(None),
            )
            if connector:
                query = query.filter(ListingObservation.source == connector)
            if product_id:
                query = query.filter(ListingObservation.product_id == product_id)

            listings = (
                query.order_by(ListingObservation.last_seen_at.desc())
                .limit(sample_size)
                .all()
            )
            for l in listings:
                db.expunge(l)

        if not listings:
            return {"status": "no_listings"}

        from ingestion.audit import audit_listings, compute_connector_accuracy

        records = await audit_listings(listings, audit_mode="on_demand")

        with SessionLocal() as db:
            for r in records:
                db.add(r)
            db.commit()

        accuracy = compute_connector_accuracy(records)
        return {"status": "success", "audited": len(records), "accuracy": accuracy}

    finally:
        # Release lock
        try:
            r = redis_lib.from_url(settings.redis_url)
            r.delete("audit:on_demand:running")
        except Exception:
            pass
```

Register in `WorkerSettings.functions`:
```python
        run_on_demand_audit,
```

- [ ] **Step 3: Include audit router in main app**

In `backend/main.py`, add after existing router includes (around line 51):

```python
from backend.routers.audit import router as audit_router
app.include_router(audit_router)
```

- [ ] **Step 4: Commit**

```bash
git add backend/routers/audit.py backend/main.py ingestion/worker.py
git commit -m "feat: add audit API endpoints and on-demand ARQ task"
```

---

### Task 9: Health Overview Integration

**Files:**
- Modify: `backend/routers/health.py:179-256`

- [ ] **Step 1: Add connector quality to health overview**

In `backend/routers/health.py`, in the `get_health_overview` function, add after the `precision` computation (around line 254), before the return statement:

```python
    # Connector audit quality (last 7 days)
    audit_cutoff = datetime.now(UTC) - timedelta(days=7)
    audit_records = (
        db.query(ConnectorAudit)
        .filter(ConnectorAudit.audited_at >= audit_cutoff)
        .all()
    )

    connector_quality = {}
    if audit_records:
        from ingestion.audit import compute_connector_accuracy

        connector_quality = compute_connector_accuracy(audit_records)
        for source_data in connector_quality.values():
            # Add last_audit timestamp
            source_audits = [r for r in audit_records if r.source == source_data.get("source")]
            if source_audits:
                source_data["last_audit"] = max(r.audited_at for r in source_audits).isoformat()
```

Add `connector_quality` to the return dict:

```python
    "connector_quality": connector_quality,
```

Add `ConnectorAudit` to the imports at the top of `health.py`:

```python
from libs.common.models import ConnectorAudit, IngestionRun, ProductTemplate
```

- [ ] **Step 2: Commit**

```bash
git add backend/routers/health.py
git commit -m "feat: add connector_quality to /health/overview"
```

---

### Task 10: Lint, Test, Final Commit

- [ ] **Step 1: Run linting and formatting**

```bash
uv run ruff check --fix . && uv run ruff format .
```

- [ ] **Step 2: Run full test suite**

```bash
uv run pytest tests/unit/ -v
```

Expected: All tests PASS (unit tests only — integration tests require DB).

- [ ] **Step 3: Commit if any formatting changes**

```bash
git add -A && git commit -m "style: lint and format"
```

# Connector Data Quality Audit — Design Spec

**Date:** 2026-03-17
**Status:** Approved
**Author:** Guillaume + Claude
**Priority:** Pre-M2 — must validate connector output before building scoring on top of it

## Goal

Verify that marketplace connectors (eBay, LeBonCoin, Vinted) extract correct data from real production pages. Two-pronged: an initial full audit via CLI, and ongoing continuous monitoring via sampling. Uses LLM-as-judge with both screenshots and raw HTML to compare extracted fields against actual page content.

---

## 1. LLM Judge

### Input

For each listing to audit, the judge receives:
- **Screenshot** of the listing page (resized to max 1920x3000px to control token cost)
- **Raw HTML** of the listing page (truncated to relevant section if >50KB)
- **Extracted fields**: `{title, price, currency, condition, location, seller_rating, shipping_cost, is_sold}`

Both screenshot and HTML are captured in a single Playwright page visit (see Section 10). The judge is a standalone async LLM call in `ingestion/audit.py` — it does **not** reuse `assess_listing_relevance` from `llm_service.py` (different prompt, different response schema, different purpose).

### Prompt

The LLM compares extracted values against what it sees on the page/HTML. Returns a structured verdict per field.

### Response Format

```json
{
  "fields": {
    "price": {"verdict": "correct", "expected": "85.00", "extracted": "85.0"},
    "title": {"verdict": "correct"},
    "condition": {"verdict": "incorrect", "expected": "Très bon état", "extracted": null},
    "is_sold": {"verdict": "correct"},
    "location": {"verdict": "unverifiable", "reason": "not visible on page"},
    "seller_rating": {"verdict": "correct"},
    "shipping_cost": {"verdict": "unverifiable", "reason": "requires interaction"}
  },
  "overall": "partial_match",
  "notes": "Condition field not extracted despite being visible on page"
}
```

Verdicts: `correct`, `incorrect`, `unverifiable`. Accuracy computed over verifiable fields only.

---

## 2. Schema

New table `connector_audit`:

```
audit_id          UUID PK
ingestion_run_id  UUID FK → ingestion_run (nullable)
obs_id            BIGINT FK → listing_observation
source            TEXT (ebay/leboncoin/vinted)
audit_mode        TEXT ('continuous', 'on_demand', 'cli')
screenshot_path   TEXT (nullable)
html_snippet      TEXT (truncated raw HTML, nullable)
llm_response      JSONB (full LLM verdict — JSONB for query support)
field_results     JSONB ({field: verdict} summary — JSONB for aggregation)
accuracy_score    NUMERIC(3,2) (0-1, fraction of verifiable fields correct)
audited_at        TIMESTAMP WITH TIME ZONE
cost_tokens       INTEGER (LLM token usage)
```

---

## 3. Three Modes

### 3a. Continuous Sampling

After each ingestion run, sample `AUDIT_SAMPLE_SIZE` listings (default 3, configurable). Only listings with a URL. Runs as a separate ARQ task (non-blocking). Results accumulate in `connector_audit`.

To wire this, `run_full_ingestion` must return the `ingestion_run_id` values from each connector run (currently tracked internally by `track_ingestion_run` but not surfaced). The post-ingestion audit task queries recent `listing_observation` rows for the run and samples from those.

Budget: ~3 listings × 24 runs/day × ~$0.05/audit ≈ $3.60/day ≈ €100/month at full rate. In practice, lower: not every run produces new listings to audit, and the daily token budget caps spending.

### 3b. On-Demand API Audit

`POST /audit/connectors` — triggers a moderate audit from recent listings.

Params: `connector` (optional), `sample_size` (default 20), `product_id` (optional).

Enqueues ARQ task. Returns job ID. Results at `GET /audit/connectors/{job_id}` (returns 202 while running, 200 with results when done, error payload on failure).

**Concurrency guard:** Only one on-demand audit runs at a time. The API checks for an existing running audit ARQ job (via Redis key) and returns 409 if one is active.

### 3c. CLI Full Audit

Triggered via:
```
uv run python -m ingestion.audit_cli [OPTIONS]
```

Runs a real full ingestion cycle, then audits **every** ingested listing (not sampled). Produces detailed Markdown reports.

**CLI options:**
```
Options:
  --connectors TEXT                Comma-separated: ebay,leboncoin,vinted (default: all)
  --products-per-connector INT    Products to test per connector (default: 5)
  --listings-per-product INT      Ingestion limit per product (default: 20)
  --output-dir TEXT               Report directory (default: reports/connector-audit-YYYY-MM-DD/)
  --skip-ingestion                Audit existing recent listings instead of fresh scrapes
  --product-ids TEXT              Specific product IDs (comma-separated, overrides --products-per-connector)
  --html-only                     Skip screenshots, judge from HTML only (much cheaper, ~10x)
```

**Cost estimate for CLI mode:** With screenshot+HTML, a full audit of 300 listings costs ~€15-30 (vision tokens dominate). With `--html-only`, ~€1.50-3. The `--html-only` flag is recommended for frequent runs; full screenshot mode for deep investigations.

**Report structure:**
```
reports/connector-audit-YYYY-MM-DD/
├── summary.md           # Cross-connector comparison, overall verdict
├── ebay.md              # eBay detailed report
├── leboncoin.md         # LeBonCoin detailed report
└── vinted.md            # Vinted detailed report
```

**Per-connector report contents:**

- Summary: products tested, listings ingested/audited, overall accuracy, pass/fail verdict
- Per-field accuracy table (correct/incorrect/unverifiable/accuracy%)
- Failure analysis per field: most common issue, root cause hypothesis, example failures with listing IDs and URLs
- Raw data table (collapsible) with per-listing results

**`--skip-ingestion`** audits data from the most recent production runs without triggering new scrapes. Useful for post-incident analysis.

---

## 4. Health Integration

### `/health/overview` — `connector_quality` section

```json
{
  "connector_quality": {
    "ebay": {"accuracy": 0.94, "sample_size": 72, "last_audit": "...", "status": "green"},
    "leboncoin": {"accuracy": 0.87, "sample_size": 68, "last_audit": "...", "status": "yellow"},
    "vinted": {"accuracy": 0.71, "sample_size": 45, "last_audit": "...", "status": "red"}
  }
}
```

Status thresholds (configurable):
- Green: >= 90% accuracy
- Yellow: >= 80%
- Red: < 80% → triggers Telegram alert

Accuracy computed from `connector_audit` rows in the last 7 days.

### `GET /audit/connectors/results`

Detailed view: per-field accuracy breakdown, worst-performing fields, recent failures with LLM notes. Params: `connector`, `days` (default 7).

---

## 5. Telegram Alert

When continuous sampling detects a connector dropping below threshold, fires via a new `send_connector_quality_alert` function (the existing `send_system_alert` has a fixed signature for stale products/failing connectors and cannot be reused):

```
⚠️ Connector Quality Alert

🔴 vinted: accuracy 71% (threshold 80%)
  - price: 92% ✓
  - condition: 45% ✗ ← likely selector change
  - title: 78% ✗

Last 7d: 45 listings audited

Action: check vinted connector for HTML structure changes
```

---

## 6. Cost Control

- Track `cost_tokens` per audit row
- Setting: `AUDIT_DAILY_TOKEN_BUDGET` (default 100K tokens ≈ €2/day)
- Once budget exhausted for the day, skip continuous sampling (log warning)
- On-demand and CLI audits always run regardless of daily budget (explicit user action)

---

## 7. Settings

```python
# Connector audit
audit_sample_size: int = 3              # listings per ingestion run (continuous mode)
audit_accuracy_green: float = 0.90      # green threshold
audit_accuracy_yellow: float = 0.80     # yellow threshold (below = red + alert)
audit_daily_token_budget: int = 100000  # daily token budget for continuous sampling
audit_enabled: bool = True              # master switch for continuous sampling
```

---

## 8. Migration

Add to the M2 migration (`0006_milestone2_frequency_scoring.py`):

```sql
CREATE TABLE connector_audit (
    audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_run_id UUID REFERENCES ingestion_run(run_id),
    obs_id BIGINT NOT NULL REFERENCES listing_observation(obs_id),
    source TEXT NOT NULL,
    audit_mode TEXT NOT NULL CHECK (audit_mode IN ('continuous', 'on_demand', 'cli')),
    screenshot_path TEXT,
    html_snippet TEXT,
    llm_response JSONB,
    field_results JSONB,
    accuracy_score NUMERIC(3,2),
    audited_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    cost_tokens INTEGER
);

CREATE INDEX ix_connector_audit_source_date ON connector_audit(source, audited_at);
CREATE INDEX ix_connector_audit_obs ON connector_audit(obs_id);
```

---

## 9. Page Capture Strategy

The existing `capture_listing_screenshot` in `screenshot_service.py` launches a new Chromium instance per call. This is fine for continuous sampling (3 listings) but unacceptable for CLI mode (potentially 300 listings = 300 browser launches).

The audit module manages its own Playwright browser context with a batch capture interface:

```python
async def capture_audit_batch(
    listings: list[ListingObservation],
    html_only: bool = False,
) -> dict[int, AuditCapture]:
    """
    Open one browser, visit each listing URL, capture screenshot + HTML.
    Returns {obs_id: AuditCapture(screenshot_path, html_snippet)}.
    """
```

- One browser launch for the entire batch
- Reuses the stealth session configuration from `libs/common/session.py` (Playwright stealth patches, UA rotation)
- Inter-page delay of 2-3 seconds to avoid rate limiting
- Screenshots resized to max 1920x3000px before saving (controls LLM token cost)
- HTML captured via `page.content()` in the same visit, truncated to 50KB
- If `html_only=True`, skip screenshot capture entirely

### Anti-Bot Handling

Marketplaces (especially Vinted and LeBonCoin) aggressively detect automated access. The audit re-visits pages that were already scraped, which is a second visit to the same URL.

- **Stealth patches:** Reuse the same Playwright stealth configuration as the scraping session (`libs/common/session.py`)
- **Rate limiting:** 2-3 second delay between page visits; randomized to avoid pattern detection
- **CAPTCHA/login wall detection:** Before sending to LLM, check if the page content contains known CAPTCHA indicators (e.g., "captcha", "robot", "verify you are human") or login forms. If detected:
  - Mark all fields as `unverifiable` with reason `blocked_by_antibot`
  - Skip LLM call (saves tokens)
  - Log warning with URL for manual investigation
- **Budget impact:** Blocked pages don't count against the daily token budget since no LLM call is made

---

## 10. Files

| File | Change |
|------|--------|
| `migrations/versions/0006_...py` | Add `connector_audit` table |
| `libs/common/models.py` | Add `ConnectorAudit` model |
| `libs/common/settings.py` | Add audit settings |
| `ingestion/audit.py` | **New** — LLM judge logic, batch page capture, field comparison, accuracy computation, report generation |
| `ingestion/audit_cli.py` | **New** — CLI entry point: orchestrate ingestion + audit + Markdown report generation |
| `ingestion/ingestion.py` | Modify `run_full_ingestion` to return `ingestion_run_id` values |
| `ingestion/worker.py` | Add `audit_ingestion_sample` ARQ task, wire post-ingestion |
| `backend/routers/audit.py` | **New** — `POST /audit/connectors`, `GET /audit/connectors/results`, `GET /audit/connectors/{job_id}` |
| `backend/routers/health.py` | Add `connector_quality` to overview |
| `libs/common/telegram_service.py` | Add `send_connector_quality_alert` function |
| `tests/unit/test_audit.py` | **New** — verdict parsing, accuracy calculation, threshold/alert logic, CAPTCHA detection |

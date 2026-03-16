# Milestone 2: Fast and Precise — Design Spec

**Date:** 2026-03-16
**Status:** Approved
**Author:** Guillaume + Claude
**Branch:** `feature/milestone2-week5`

## Goal

Alerts are high-quality and arrive fast enough to act on. End-to-end latency target: 1h15 from listing appearance to Telegram alert.

**Exit criteria:** Alert precision >80%. Acting on 1+ deal per week. Pipeline latency under 1h15.

---

## 1. Higher Ingestion Frequency

### Problem
Ingestion runs once daily per connector (eBay 2AM, LeBonCoin 3AM, Vinted 4AM). Opportunities go stale before they're surfaced.

### Design

Add `ingestion_interval_hours` (Integer, default 24) column to `product_template`. Change connector cron jobs from once/day to **every hour**. Modify `_active_product_ids()` to accept a `due_only=True` parameter: returns only products where `last_ingested_at + interval_hours < now()` or `last_ingested_at IS NULL`.

After each product's ingestion completes, **immediately trigger computation + scoring + alerting** inline — no waiting for the 5 AM batch computation cron. The existing `scheduled_computation` cron at 5 AM remains as a daily catch-up safety net (recomputes all active products to handle any missed runs).

### Schema Change

```sql
ALTER TABLE product_template ADD COLUMN ingestion_interval_hours INTEGER NOT NULL DEFAULT 24;
```

### Behavior

- Cron runs every hour for each connector, **staggered by 10 minutes** (eBay at :00, LeBonCoin at :10, Vinted at :20) to avoid concurrent connector races on shared products
- `_active_product_ids(provider, due_only=True)` filters to products due for ingestion
- After ingestion of each product: compute PMN → compute score → run alert pipeline
- High-priority products set `ingestion_interval_hours=1`, normal products keep `24`
- `last_ingested_at` on `product_template` already updated by the run tracker

### Multi-Source PMN Recomputation

When a product has multiple providers (e.g. eBay + Vinted), the first connector to ingest will recompute PMN based on all existing sold data (including prior runs from other connectors). The PMN will be slightly stale w.r.t. the second connector's data arriving later that cycle. This is acceptable — the daily 5 AM catch-up recomputation ensures full accuracy, and the staggered crons mean the gap is only ~10-20 minutes.

---

## 2. Composite Opportunity Score

### Problem
Current alert engine uses a simple "price < PMN" check. No ranking, no way to separate great deals from marginal ones.

### Design

**Replaces** the existing `compute_opportunity_score()` in `ingestion/computation.py` (lines 436+), which uses a simpler 3-factor formula (margin 40pts, liquidity 30pts, risk 30pts). The old function is removed and all callers updated to use the new module.

New module `ingestion/scoring.py` with a single public function:

```python
def compute_opportunity_score(
    listing: ListingObservation,
    pmn_data: MarketPriceNormal,
    metrics: ProductDailyMetrics | None,
    llm_result: dict | None = None,
) -> dict:
    """Returns {"score": float 0-100, "breakdown": {...}}"""
```

Score computed on-the-fly (no stored column). Inputs change frequently; storing would create staleness issues. For the discovery endpoint, scores are computed at query time for the filtered result set (typically <50 listings per product after PMN filtering), which is fast enough since all inputs are already loaded in the query.

### Formula

| Factor | Weight | Calculation | Range |
|--------|--------|-------------|-------|
| Margin vs PMN | 35% | `(pmn - price) / pmn` clamped to [0, 1] | 0 = at PMN, 1 = free |
| Liquidity | 20% | `metrics.liquidity_score / 100` (already 0-100) | 0 = illiquid, 1 = highly liquid |
| Listing freshness | 15% | `max(0, 1 - hours_since_first_observed / 168)` (uses `observed_at`, decays over 7 days) | 0 = week old, 1 = just listed |
| PMN confidence | 15% | `pmn_data.confidence` (already 0-1) | 0 = unreliable, 1 = solid |
| Seller rating | 10% | `min(seller_rating / 5.0, 1.0)` or 0.5 if missing | 0 = bad, 1 = perfect |
| LLM assessment | 5% | 1.0 if LLM confirms relevant, 0.0 if rejects, 0.5 if not run | bonus/penalty |

Final score = `sum(weight * sub_score) * 100`, yielding 0-100.

### Return Value

```python
{
    "score": 72.5,
    "breakdown": {
        "margin": {"raw": 0.23, "weighted": 8.05},
        "liquidity": {"raw": 0.85, "weighted": 17.0},
        "freshness": {"raw": 0.95, "weighted": 14.25},
        "pmn_confidence": {"raw": 0.78, "weighted": 11.7},
        "seller_rating": {"raw": 0.90, "weighted": 9.0},
        "llm": {"raw": 0.5, "weighted": 2.5},
    }
}
```

---

## 3. Tiered Alerting

### Problem
All opportunities that pass the price threshold get the same treatment — Telegram alert. This creates noise.

### Design

Two global settings in `settings.py`:
- `ALERT_TELEGRAM_THRESHOLD` (default 80) — score above this triggers Telegram
- `ALERT_DASHBOARD_THRESHOLD` (default 50) — score above this is persisted

Behavior:
- **Score >= 80:** Telegram alert sent + `alert_event` persisted with `tier="telegram"`
- **Score 50-79:** `alert_event` persisted with `tier="dashboard"` (visible in UI, no Telegram)
- **Score < 50:** Suppressed. Logged at DEBUG level, not persisted.

### Changes to Alert Engine

The existing `trigger_alerts()` in `ingestion/alert_engine.py` currently evaluates `AlertRule` objects (with `threshold_pct`, `min_margin_abs`, etc.) and creates `AlertEvent` records linked to a `rule_id`. The new score-based tiering **replaces** alert rule evaluation as the primary gating mechanism. `AlertRule` evaluation is removed — the composite score subsumes all the individual thresholds that rules checked.

Updated flow:

1. Filter listings where `price < pmn` and `is_sold=False` and `is_stale=False` (existing)
2. For each candidate, call `compute_opportunity_score()`
3. Apply tier logic based on score
4. For telegram-tier: run LLM validation if enabled (see section 4), then send alert
5. Telegram message includes score and top 3 contributing factors
6. `alert_event.rule_id` set to NULL for score-based alerts (column kept for backward compatibility with M1 data)

### Deduplication

Duplicate detection updated: check by `obs_id` only (not `rule_id + obs_id`), since score-based alerts have no rule. A listing that was already alerted in a previous run is not re-alerted regardless of tier.

### Schema Change

Add `tier` (text, CHECK constraint: `tier IN ('telegram', 'dashboard')`), `opportunity_score` (Numeric, nullable) columns to `alert_event` table. Allows filtering and analytics by tier.

### Discovery Endpoint

`/products/discovery` response gains `opportunity_score` and `score_breakdown` fields per listing. Results sorted by score descending by default.

---

## 4. LLM Validation

### Problem
LLM validation exists (`validate_listing_with_llm`, `assess_listing_relevance`) but is not integrated into the alert pipeline. Needs activation with Gemini.

### Design

LLM runs **post-score, pre-alert** — only on listings that score above the dashboard threshold (>50). This keeps costs within the ~€20/month budget (LLM only evaluates the top candidates).

Flow:
1. Opportunity scores above dashboard threshold
2. If `product_template.enable_llm_validation` is True and `GEMINI_API_KEY` is set:
   - Call `assess_listing_relevance()` with listing + product context
   - Include recent feedback stats in the prompt: "For this product, 73% of past alerts were marked interested" — gives the LLM calibration context
3. LLM result updates the score's LLM component (5% weight):
   - `is_relevant=True` → 1.0 (score gets +5 boost max)
   - `is_relevant=False` → 0.0 (score gets -2.5 penalty from the 0.5 default)
   - LLM failure/timeout → 0.5 (neutral, no impact)
4. Recalculated score determines final tier (a listing at 78 could be boosted to 83 → Telegram)

### Prompt Enhancement

Add to the existing LLM prompt:
- Feedback stats for this product (interested rate, purchase rate)
- PMN and confidence context
- Instruction: "Reject listings that are likely accessories, bundles, or wrong variants"

### Cost Control

- LLM only runs on listings scoring >50 (est. 10-20% of all listings)
- Keep existing `@retry(stop=stop_after_attempt(3))` decorator for transient API errors (timeouts, 5xx). No retry on LLM rejection (`is_relevant=False`) — that's a valid response, not an error.
- Daily cost tracked via a counter; log warning if approaching budget

---

## 5. Pipeline Latency

### Problem
No visibility into how long each pipeline stage takes. Need to ensure the full ingestion→compute→score→alert path completes within 15 minutes (the processing budget within the 1h15 target).

### Design

Add timestamp tracking through the pipeline:
- `ingestion_started_at` — already captured in `IngestionRun.started_at`
- `sent_at` — already exists on `alert_event`, currently set to `datetime.now(UTC)` at creation time. This is the Telegram delivery timestamp.

Link alerts to their triggering ingestion run via a new `ingestion_run_id` FK on `alert_event`.

Latency metric: `sent_at - IngestionRun.started_at` (joined via `ingestion_run_id`) for each alert.

### Health Endpoint

Add to `/health/overview` response:
```json
{
    "latency": {
        "avg_ingestion_to_alert_minutes": 4.2,
        "p95_ingestion_to_alert_minutes": 11.5,
        "alerts_within_target_pct": 98.5
    }
}
```

Computed from `alert_event` records in the last 24h that have `ingestion_run_id` set and `sent_at` populated.

### Schema Change

Add `ingestion_run_id` (UUID FK → `ingestion_run.run_id`, nullable) to `alert_event`.

---

## 6. Minor M1 Cleanup

Add `pmn_confidence` field to the `/products/{product_id}` endpoint response (`ProductDetail` model in `backend/main.py`). The data is already in `market_price_normal` — just needs to be surfaced.

---

## Migration Summary

Single Alembic migration `0006_milestone2_frequency_scoring.py`:

```
product_template:
  + ingestion_interval_hours  INTEGER NOT NULL DEFAULT 24

alert_event:
  + tier                      TEXT (nullable, CHECK IN ('telegram', 'dashboard'))
  + opportunity_score         NUMERIC (nullable)
  + ingestion_run_id          UUID FK → ingestion_run.run_id (nullable)
```

---

## Files Changed (Estimated)

| File | Change |
|------|--------|
| `libs/common/models.py` | Add columns to ProductTemplate, AlertEvent |
| `libs/common/settings.py` | Add threshold + interval settings |
| `migrations/versions/0006_...py` | New migration |
| `ingestion/scoring.py` | **New** — score computation (replaces `compute_opportunity_score` from `computation.py`) |
| `ingestion/alert_engine.py` | Integrate scoring, tiered alerting, LLM gating; remove `AlertRule` evaluation |
| `ingestion/worker.py` | Hourly staggered crons, inline compute+alert after ingestion, `due_only` filter |
| `ingestion/computation.py` | Remove old `compute_opportunity_score()`; called inline after ingestion |
| `backend/main.py` | Add pmn_confidence to ProductDetail, score to discovery |
| `backend/routers/health.py` | Add latency stats to overview |
| `libs/common/telegram_service.py` | Include score + factors in alert message |
| `tests/unit/test_scoring.py` | **New** — score computation tests |
| `tests/unit/test_alert_engine.py` | **New** — tiered alerting tests |

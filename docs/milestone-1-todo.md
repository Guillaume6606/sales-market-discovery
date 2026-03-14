# Milestone 1: Trust the System — Detailed Tasks

**Goal:** Platform runs unattended and you trust what it tells you.
**Timeline:** Weeks 1-4
**Exit criteria:** 7 consecutive days without silent failures. Ingestion health visible at a glance. PMN accuracy is measured.

Spec: `docs/superpowers/specs/2026-03-14-roadmap-objectives-kpis-design.md`

---

## 1. Observability & Health Monitoring

### 1.1 Ingestion run tracking

- [ ] Create `ingestion_run` table: `run_id` (BigInt PK), `connector` (text: ebay/leboncoin/vinted), `run_type` (text: sold/listings), `product_id` (UUID FK), `started_at`, `finished_at`, `status` (success/error/no_data), `listings_fetched` (int), `listings_filtered` (int), `listings_persisted` (int), `error_message` (text), `duration_ms` (int)
- [ ] Add `IngestionRun` SQLAlchemy model in `libs/common/models.py`
- [ ] Create Alembic migration for new table
- [ ] Instrument each ingestion function in `ingestion/ingestion.py` to create an `IngestionRun` record on start and update on completion (with counts and timing)
- [ ] Instrument `filter_listings_multi_stage()` to return stage-level rejection counts (already returns `FilteringStats`, wire it into the run record)

### 1.2 Health monitoring endpoints

- [ ] `GET /health/ingestion` — Per-connector summary: last successful run, last failure, success rate (24h/7d), avg duration, total listings processed
- [ ] `GET /health/products` — Per-product staleness: last_ingested_at, hours since last ingestion, flagged if >24h stale
- [ ] `GET /health/overview` — Single-page health dashboard data: all connectors green/yellow/red, stale product count, queue depth, last 10 ingestion runs with status

### 1.3 Staleness alerts

- [ ] Add ARQ cron job `check_system_health` running every 2 hours
- [ ] Detect products where `last_ingested_at` is >24h ago (configurable threshold)
- [ ] Detect connectors with >3 consecutive failures
- [ ] Send system health Telegram message (separate format from opportunity alerts, e.g. prefixed with warning icon instead of target icon) via `telegram_service.py`
- [ ] Add `send_system_alert()` function to `libs/common/telegram_service.py`

### 1.4 Ingestion run history endpoint

- [ ] `GET /ingestion/runs` — Paginated list of recent ingestion runs with filters (connector, status, product_id, date range)
- [ ] `GET /ingestion/runs/{run_id}` — Detail view with full stats

---

## 2. PMN Validation & Backtesting

### 2.1 PMN confidence scoring

- [ ] Add `confidence` (Numeric, 0-1) column to `market_price_normal` table
- [ ] Create Alembic migration
- [ ] Implement confidence formula in `ingestion/computation.py`:
  - Sample size factor: `min(sample_size / 30, 1.0)` — 30+ sold items = full confidence
  - Freshness factor: `max(0, 1 - days_since_newest_sale / 30)` — decays if no recent sales
  - Consistency factor: `1 - (std_dev / pmn)` clamped to [0, 1] — low variance = high confidence
  - Final: weighted average of three factors (e.g. 0.4 * sample + 0.3 * freshness + 0.3 * consistency)
- [ ] Store confidence in `MarketPriceNormal` on each PMN computation
- [ ] Suppress opportunity alerts for products with confidence < 0.3 (configurable threshold in settings)

### 2.2 PMN backtesting

- [ ] Create `pmn_history` table: `id` (BigInt PK), `product_id` (UUID FK), `computed_at` (timestamp), `pmn` (numeric), `pmn_low`, `pmn_high`, `confidence`, `sample_size` (int)
- [ ] On each PMN recomputation, insert a row into `pmn_history` (keeps the old value before overwriting `market_price_normal`)
- [ ] `GET /products/{product_id}/pmn-accuracy` — Compare historical PMN values against actual sold prices in the same period: mean absolute error, mean % error, hit rate (% of sold prices within PMN low/high bounds)
- [ ] `GET /analytics/pmn-accuracy` — Aggregate accuracy across all products: overall MAE, worst/best products, products with <0.3 confidence

### 2.3 Surface confidence in existing endpoints

- [ ] Include `pmn_confidence` in `/products/discovery` response
- [ ] Include `pmn_confidence` in `/products/{product_id}` response
- [ ] Add `min_pmn_confidence` query parameter to `/products/discovery` (default: 0, so no breaking change)
- [ ] Show confidence badge in Telegram opportunity alerts (e.g. "PMN confidence: high/medium/low")

---

## 3. Test Coverage

### 3.1 Test infrastructure setup

- [ ] Create `tests/` directory structure: `tests/unit/`, `tests/integration/`, `tests/conftest.py`
- [ ] Configure pytest in `pyproject.toml` (testpaths, asyncio_mode=auto)
- [ ] Create shared fixtures in `conftest.py`: sample `Listing` objects, sample `ProductTemplateSnapshot`, mock DB session (SQLite in-memory for unit tests)
- [ ] Add `pytest-cov` to dev dependencies for coverage reporting

### 3.2 Unit tests — pricing (`ingestion/pricing.py`)

- [ ] Test `pmn_from_prices()` with n < 3 (returns simple median, no bounds)
- [ ] Test `pmn_from_prices()` with 3 <= n < 20 (percentile filtering, std bounds)
- [ ] Test `pmn_from_prices()` with n >= 20 (time-weighted median)
- [ ] Test `pmn_from_prices()` with empty list (returns None PMN)
- [ ] Test `pmn_from_prices()` with identical prices (zero std dev)
- [ ] Test `pmn_from_prices()` with extreme outliers (verify 5-95 percentile clipping)
- [ ] Test `iqr_clip()` edge cases

### 3.3 Unit tests — filtering (`ingestion/filtering.py`)

- [ ] Test `_matches_price()`: within range, below min, above max, None price, None bounds
- [ ] Test `_matches_brand()`: brand in search query (skip filter), brand in title, brand in listing.brand field, brand not found
- [ ] Test `_matches_words_to_avoid()`: no words to avoid, single match, no match, case insensitivity
- [ ] Test `filter_listings_multi_stage()`: end-to-end with mixed listings, verify stats counts are correct

### 3.4 Unit tests — computation (`ingestion/computation.py`)

- [ ] Test `compute_pmn_for_product()`: sufficient sold items, fallback to active listings, insufficient data
- [ ] Test `compute_liquidity_score()`: high velocity, low velocity, no sales
- [ ] Test `estimate_margin()`: per-platform fee deduction (eBay 15.9%, LeBonCoin 8%, Vinted 8%)
- [ ] Test confidence scoring (once implemented in 2.1)

### 3.5 Unit tests — connector parsing

- [ ] eBay: mock Finding API JSON response, verify `Listing` fields extracted correctly
- [ ] LeBonCoin API: mock JSON response, verify parsing
- [ ] Vinted: mock HTML response, verify scraping extracts correct fields
- [ ] Test condition normalization across connectors

### 3.6 Integration tests

- [ ] Ingestion → filtering → persistence: mock connector, verify listings land in DB with correct fields
- [ ] PMN computation: seed DB with listing_observations, run `compute_pmn_for_product()`, verify `market_price_normal` row
- [ ] Alert pipeline: seed DB with PMN + listings below PMN + alert rule, run `trigger_alerts()`, verify `alert_event` created

---

## 4. Feedback Loop

### 4.1 Feedback data model

- [ ] Create `alert_feedback` table: `feedback_id` (BigInt PK), `alert_id` (BigInt FK → alert_event), `action` (text: interested/not_interested/purchased/ignored), `responded_at` (timestamp), `notes` (text, nullable), `profit` (numeric, nullable — filled later when item is resold)
- [ ] Add `AlertFeedback` SQLAlchemy model
- [ ] Create Alembic migration

### 4.2 Telegram inline keyboard

- [ ] Modify `send_opportunity_alert()` in `telegram_service.py` to include inline keyboard with two buttons: "Interested" / "Not interested"
- [ ] Store the `alert_id` in the callback data so we can link responses back
- [ ] Create `send_opportunity_alert_with_feedback()` or extend existing function

### 4.3 Telegram webhook for feedback

- [ ] Add `POST /webhooks/telegram` endpoint in `backend/main.py` to receive callback query updates
- [ ] Parse callback data, extract `alert_id` and `action`
- [ ] Insert `AlertFeedback` record
- [ ] Send Telegram `answerCallbackQuery` to acknowledge the button press
- [ ] Optionally: add "Purchased" button that appears after "Interested" is pressed (second interaction)

### 4.4 Feedback API endpoints

- [ ] `POST /alerts/events/{alert_id}/feedback` — Manual feedback submission (for dashboard use)
- [ ] `GET /alerts/events/{alert_id}/feedback` — Get feedback for a specific alert
- [ ] `PATCH /alerts/feedback/{feedback_id}` — Update feedback (e.g. add profit after resale)

### 4.5 Precision tracking

- [ ] `GET /analytics/alert-precision` — Compute precision metrics from feedback data:
  - Total alerts sent (last 7d/30d)
  - Feedback response rate
  - Interested rate (interested / (interested + not_interested))
  - Purchase rate (purchased / total with feedback)
  - Breakdown by product category and connector
- [ ] Include precision summary in `/health/overview`

---

## 5. Data Quality

### 5.1 Stale listing detection

- [ ] Add `last_seen_at` column to `listing_observation` (updated on every upsert, distinct from `observed_at` which is the marketplace timestamp)
- [ ] Create Alembic migration
- [ ] Update `_upsert_listing()` in `ingestion/ingestion.py` to set `last_seen_at = now()` on every upsert
- [ ] Add ARQ task `mark_stale_listings` (daily): mark listings as stale where `last_seen_at < now() - 7 days` and `is_sold = False`. Add `is_stale` boolean column, or simply exclude them from opportunity queries
- [ ] Exclude stale listings from opportunity detection in `ingestion/ingestion.py` and `ingestion/worker.py`

### 5.2 Missing data tracking

- [ ] Add per-connector metrics to `IngestionRun` (from task 1.1): `listings_missing_price` (int), `listings_missing_title` (int)
- [ ] In each connector, count and log listings that fail price extraction
- [ ] Surface in `/health/ingestion` endpoint: per-connector missing data rates

### 5.3 Deduplication improvements

- [ ] Add unique constraint on `(source, listing_id, product_id)` in `listing_observation` if not already present (prevents race condition duplicates from concurrent workers)
- [ ] Handle `IntegrityError` gracefully in `_upsert_listing()` — if duplicate key, treat as update

### 5.4 Data validation on ingestion

- [ ] Reject listings with `price <= 0` or unreasonable prices (>50K EUR) — log and count rejections
- [ ] Reject listings with empty or whitespace-only titles
- [ ] Log warnings for listings missing URL (impacts screenshot capture later)

---

## Task Dependencies

```
1.1 (ingestion_run table) ← 1.2 (health endpoints) ← 1.3 (staleness alerts)
2.1 (confidence scoring)  ← 2.2 (backtesting) ← 2.3 (surface in API)
3.1 (test infra)          ← 3.2-3.6 (all test tasks)
4.1 (feedback model)      ← 4.2 (inline keyboard) ← 4.3 (webhook)
4.1 (feedback model)      ← 4.4 (feedback API) ← 4.5 (precision tracking)
5.1 (stale detection)     — independent
5.3 (dedup constraint)    — independent
```

## Suggested Execution Order

**Week 1:** 1.1, 3.1, 4.1, 5.3, 5.4 (foundations: tables, test infra, constraints)
**Week 2:** 1.2, 1.3, 2.1, 3.2, 3.3, 5.1 (health monitoring, confidence, core tests)
**Week 3:** 2.2, 2.3, 3.4, 3.5, 4.2, 4.3, 5.2 (backtesting, connector tests, telegram feedback)
**Week 4:** 1.4, 3.6, 4.4, 4.5 (remaining endpoints, integration tests, precision tracking)

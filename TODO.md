# TODO — Market Discovery & Arbitrage Platform

Reference spec: `docs/superpowers/specs/2026-03-14-roadmap-objectives-kpis-design.md`

---

## Milestone 1: Trust the System (Weeks 1-4)

### Observability & Health Monitoring (Weeks 1-2)
- [x] Create `ingestion_run` table and `IngestionRun` model (run_id, source, status, timing, counts)
- [x] Create Alembic migration for `ingestion_run`
- [x] Instrument ingestion functions with `track_ingestion_run()` context manager
- [x] Wire `FilteringStats` into ingestion run records
- [x] `GET /health/ingestion` — per-connector success rates (24h/7d), avg duration, total listings
- [x] `GET /health/products` — per-product staleness (hours since last ingestion, stale flag)
- [x] `GET /health/overview` — system status (green/yellow/red), connector colors, stale count, recent runs
- [x] Add ARQ cron job `check_system_health` (every 2 hours)
- [x] Detect stale products (>24h since last ingestion, configurable threshold)
- [x] Detect connectors with >3 consecutive failures
- [x] Send system health Telegram alerts (separate format from opportunity alerts)
- [x] Add `send_system_alert()` to `telegram_service.py`
- [ ] `GET /ingestion/runs` — paginated list with filters (source, status, product_id, date range)
- [ ] `GET /ingestion/runs/{run_id}` — detail view with full stats

### PMN Validation & Backtesting (Weeks 2-3)
- [x] Add `confidence` column (0-1) to `market_price_normal` table + migration
- [x] Implement confidence formula: 40% sample_size + 30% freshness + 30% consistency
- [x] Store confidence on each PMN computation
- [x] Suppress opportunity alerts when PMN confidence < 0.3 (configurable)
- [x] Create `pmn_history` table (product_id, computed_at, pmn, pmn_low/high, confidence, sample_size)
- [x] Record history row on each PMN recomputation
- [x] `GET /products/{product_id}/pmn-history` — computation history for backtesting
- [x] Include `pmn_confidence` in `/products/discovery` response
- [x] Add `min_pmn_confidence` query param to `/products/discovery`
- [x] Show confidence badge (high/medium/low) in Telegram opportunity alerts
- [ ] `GET /products/{product_id}/pmn-accuracy` — MAE, % error, hit rate vs actual sold prices
- [ ] `GET /analytics/pmn-accuracy` — aggregate accuracy across all products, worst/best products

### Test Coverage (Weeks 1-3)
- [x] Create `tests/` directory structure (`unit/`, `integration/`, `conftest.py`)
- [x] Configure pytest in `pyproject.toml` (testpaths, asyncio_mode=auto)
- [x] Create shared fixtures: `sample_listing()`, `sample_snapshot()`, `listing_factory()`
- [x] Unit tests — pricing: `pmn_from_prices()` edge cases, `iqr_clip()` (7 tests)
- [x] Unit tests — filtering: `_matches_price()`, `_matches_brand()`, `_matches_words_to_avoid()` (9 tests)
- [x] Unit tests — confidence: `compute_pmn_confidence()` (8 tests)
- [x] Unit tests — validation: price/title rejection (7 tests)
- [x] Unit tests — staleness: `mark_stale_listings()` edge cases (5 tests)
- [x] Unit tests — health endpoints: ingestion health, product health, overview (5 tests)
- [x] Unit tests — connector parsing: eBay, LeBonCoin API, Vinted (26 tests)
- [ ] Unit tests — computation: `compute_pmn_for_product()`, `compute_liquidity_score()`, `estimate_margin()`
- [ ] Integration tests: ingestion → filtering → persistence (mock connector, verify DB rows)
- [ ] Integration tests: PMN computation (seed observations, run compute, verify market_price_normal)
- [ ] Integration tests: alert pipeline (seed PMN + listings + rule, run trigger_alerts, verify alert_event)

### Feedback Loop (Weeks 1, 3)
- [x] Create `alert_feedback` table (feedback_id, alert_id FK unique, feedback, notes, created_at, updated_at)
- [x] Add `AlertFeedback` model with check constraint on feedback values
- [x] Add `VALID_FEEDBACK_VALUES` constant in models.py
- [x] Telegram inline keyboard: Interested / Not Interested / Purchased buttons on alerts
- [x] `POST /webhooks/telegram` — parse callback queries, upsert feedback, answer callback, remove keyboard
- [x] Webhook secret validation (`X-Telegram-Bot-Api-Secret-Token`)
- [x] Atomic feedback upsert with PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`
- [x] `POST /alerts/events/{alert_id}/feedback` — manual feedback submission
- [x] `GET /alerts/events/{alert_id}/feedback` — retrieve feedback for an alert
- [x] `GET /analytics/alert-precision` — precision metrics (feedback rate, interested/purchased/not_interested counts)
- [ ] `PATCH /alerts/feedback/{feedback_id}` — update feedback (add profit after resale)
- [ ] Add `profit` column (Numeric, nullable) to `alert_feedback` table
- [ ] Include precision summary in `/health/overview` response

### Data Quality (Weeks 1-2)
- [x] Add unique constraint on `(source, listing_id, product_id)` in `listing_observation`
- [x] Handle `IntegrityError` gracefully in upsert — duplicate key treated as update
- [x] Reject listings with `price <= 0` or `price > 50K EUR` — log and count rejections
- [x] Reject listings with empty or whitespace-only titles
- [x] Add `last_seen_at` column to `listing_observation` (updated on every upsert)
- [x] Add `is_stale` boolean column (default false)
- [x] ARQ task `mark_stale_listings` (daily 1 AM): mark unseen listings as stale after 7 days
- [x] Exclude stale listings from opportunity queries and discovery endpoint
- [ ] Add `listings_missing_price` and `listings_missing_title` columns to `ingestion_run`
- [ ] Count missing data per connector during ingestion
- [ ] Surface missing data rates in `/health/ingestion` endpoint

### Infrastructure (Weeks 1-2)
- [x] Set up CI pipeline (GitHub Actions): lint (ruff), unit tests, Docker build
- [x] Docker Compose with 5 services: PostgreSQL 16, Redis 7, backend, ingestion worker, Streamlit UI
- [x] Alembic migrations (0001-0004)
- [x] ARQ worker with 7 cron jobs (stale detection, 3 connectors, computation, health check)
- [x] Extract shared `decimal_to_float()` to `libs/common/utils.py`

### Milestone 1 Exit Criteria
- [x] Platform runs without silent failures (health monitoring active)
- [x] Ingestion health visible at a glance (3 health endpoints)
- [ ] PMN accuracy is measured (accuracy endpoints not yet built)
- [x] Feedback loop operational (Telegram inline keyboard + precision analytics)
- [x] CI pipeline running on push
- [ ] 7 consecutive days without silent failures (requires deployment + monitoring period)

---

## Milestone 2: Fast & Precise (Weeks 5-8)

### Higher Ingestion Frequency
- [ ] Move from daily cron to every 2-4 hours for high-priority products
- [ ] Add `ingestion_frequency_hours` field to `ProductTemplate` (default: 24)
- [ ] Dynamic ARQ scheduling based on product priority
- [ ] Monitor and tune scraping rate limits per connector

### Composite Opportunity Score
- [ ] Design scoring formula: margin (40%), liquidity (30%), risk (30%) — already prototyped in `compute_opportunity_score()`
- [ ] Integrate listing age factor (penalize old listings)
- [ ] Integrate seller rating factor (prefer higher-rated sellers)
- [ ] Add LLM assessment modifier (when enabled)
- [ ] Persist opportunity scores in DB for historical analysis
- [ ] Surface score breakdown in discovery API and Telegram alerts

### Tiered Alerting
- [ ] Score >80: immediate Telegram alert
- [ ] Score 50-80: dashboard only (no Telegram)
- [ ] Score <50: suppressed entirely
- [ ] Configurable thresholds per alert rule
- [ ] Add `min_opportunity_score` to AlertRule model

### LLM Validation Activation
- [ ] Enable Gemini for top opportunities (post rule-based filtering)
- [ ] Validate LLM reduces false positives vs rules alone
- [ ] Tune prompts using feedback data from M1
- [ ] Cache LLM results per listing to control costs
- [ ] Daily budget cap for LLM API calls
- [ ] Track LLM cost per call

### Pipeline Latency Optimization
- [ ] Measure end-to-end latency: listing appears → Telegram alert
- [ ] Add timestamp tracking through pipeline stages
- [ ] Target: <15 minutes from listing appearance to alert
- [ ] Identify and eliminate bottlenecks (scraping delays, computation queue)

### Milestone 2 Exit Criteria
- [ ] Alert precision >80% (measured via feedback loop)
- [ ] Acting on 1+ deal per week
- [ ] Pipeline latency under 30 minutes
- [ ] Composite opportunity score visible on all alerts

---

## Milestone 3: Get Smarter (Weeks 9-14)

### Product Discovery Engine
- [ ] Analyze sold data across categories to identify high-volume, high-spread products
- [ ] Surface product suggestions in dashboard with supporting evidence (sold volume, price range, margin potential)
- [ ] Human approval gate: suggestions are proposals, never auto-activated
- [ ] Target: 2-3 viable suggestions per month

### Trend Detection
- [ ] Alert when a product's PMN shifts significantly (>10% in 7 days)
- [ ] Weekly digest of market movements (Telegram or dashboard)
- [ ] Price drops = buying opportunities, price rises = sell signals
- [ ] Track PMN trend direction per product (rising/falling/stable)

### Advanced LLM Analysis
- [ ] Multi-image assessment of listing photos (condition estimation)
- [ ] Red flag detection for fake or misleading listings
- [ ] Natural language listing quality assessment
- [ ] Compare listing description vs photos for consistency

### Seller Intelligence
- [ ] Track seller patterns across listings
- [ ] Flag professional resellers (competing, not arbitrage targets)
- [ ] Prefer private sellers with good ratings
- [ ] Seller history scoring integrated into opportunity score

### Dashboard v2
- [ ] Opportunity cards with photos, score breakdown, one-click actions
- [ ] Historical deal performance charts
- [ ] Product discovery suggestions page
- [ ] Market trend visualization
- [ ] Improved filtering and sorting UX

### Milestone 3 Exit Criteria
- [ ] Platform has suggested 5+ new products not manually seeded
- [ ] 50+ products actively tracked
- [ ] Revenue trending upward
- [ ] LLM filter measurably improves precision (>50% false positive reduction)

---

## Milestone 4: Scale Up (Weeks 15-20)

### Portfolio Management
- [ ] Track inventory: purchased items, resale listings, status (held/listed/sold)
- [ ] Cost basis tracking per item (purchase price + fees + shipping)
- [ ] Profit/loss per item (resale price - costs)
- [ ] Close the loop: opportunity alert → purchase → resale → realized profit
- [ ] Portfolio snapshot table + Alembic migration

### Additional Marketplaces
- [ ] Facebook Marketplace connector
- [ ] Rakuten connector
- [ ] Amazon Warehouse connector
- [ ] Unified data model across all platforms (already standardized via `Listing` model)

### Auto-Pricing for Resale
- [ ] Suggest optimal listing price per platform based on current market data
- [ ] Factor in platform fees, shipping costs, time-to-sell estimate
- [ ] Price recommendation engine using PMN + liquidity data

### Performance Analytics
- [ ] Monthly P&L dashboard
- [ ] Best/worst categories by ROI
- [ ] ROI by product type and marketplace
- [ ] Time-to-sell metrics per product category
- [ ] Revenue forecasting based on current pipeline

### Multi-Tenant Foundations (Optional)
- [ ] User accounts with authentication
- [ ] Isolated product sets per user
- [ ] Separate Telegram channels per user
- [ ] Only if SaaS path is pursued

### Milestone 4 Exit Criteria
- [ ] 1-2K EUR/month sustained over 2+ consecutive months
- [ ] <30 min/day operator time
- [ ] Clear visibility into what's working (performance analytics)
- [ ] Portfolio management tracks full buy → sell cycle

---

## Timeline Summary

```
Weeks  1-4:   M1 "Trust the System"   — reliability, testing, feedback loop
Weeks  5-8:   M2 "Fast and Precise"   — speed, scoring, alert quality
Weeks  9-14:  M3 "Get Smarter"        — discovery, trends, advanced LLM
Weeks 15-20:  M4 "Scale Up"           — portfolio, new marketplaces, analytics
```

---

## KPI Targets

### Trustworthy Signals
| KPI | Target | Status |
|-----|--------|--------|
| Alert precision | >90% of alerts are genuine opportunities | Tracking via feedback loop |
| PMN accuracy | Within ±5% of actual resale price | Not yet measured |
| LLM filter effectiveness | Reduces false positives by >50% | Not yet enabled |
| Deal conversion rate | >30% of opened alerts lead to purchase | Not yet tracked |

### Operational Reliability
| KPI | Target | Status |
|-----|--------|--------|
| Ingestion success rate | >95% of scheduled runs complete | Tracked via health endpoints |
| Data freshness | All products scraped within last 24h | Tracked via staleness alerts |
| Scraper health | <5% of listing fetches fail | Per-connector error rates in health API |
| Alert latency | <15 min from listing to Telegram | Not yet measured |

### Expanding Coverage
| KPI | Target | Status |
|-----|--------|--------|
| Active tracked products | 50+ at 3 months, 100+ at 6 months | Not yet tracked |
| Revenue per month | 500 EUR at 3 months, 1-2K at 6 months | Not yet tracked |
| New product discovery rate | 2-3 viable suggestions per month | Not yet built |
| Category diversity | 3+ categories with active deals | Not yet tracked |

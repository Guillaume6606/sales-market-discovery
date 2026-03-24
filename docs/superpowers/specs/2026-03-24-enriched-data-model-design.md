# Enriched Data Model — Design Spec

**Date:** 2026-03-24
**Status:** Draft
**Author:** Guillaume + Claude
**Depends on:** Milestone 2 spec (2026-03-16), Connector Data Quality Audit spec (2026-03-17)

## Goal

Capture the hidden variables that dictate a successful flip — logistics costs, seller psychology, temporal signals, product completeness — and compute actionable composite scores (true EUR profit, ROI, risk-adjusted confidence) so the daily 30-minute workflow surfaces only high-confidence, high-margin opportunities.

**Exit criteria:**
- All three new tables (`listing_detail`, `listing_enrichment`, `listing_score`) populated in production
- Composite scores available on dashboard: `arbitrage_spread_eur`, `net_roi_pct`, `risk_adjusted_confidence`
- Listings with `risk_adjusted_confidence < 80` filtered from default dashboard view
- Real data integration tests passing for all connectors' detail fetch
- LLM enrichment golden set accuracy ≥ 90% on boolean fields, ≥ 80% correlation on scores
- End-to-end pipeline: ingest → enrich → score completing within 2 hours

---

## Architecture Overview

Three new tables, three-stage pipeline. Each table has a single writer and single purpose.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        EXISTING PIPELINE                            │
│  Connector search API → dedupe → validate → filter → persist        │
│                                                    ↓                │
│                                          listing_observation        │
└────────────────────────────────────────────────┬────────────────────┘
                                                 │
                    ┌────────────────────────────┐│
                    │  STAGE 1: DETAIL FETCH      ││
                    │  (selective 2nd-pass)        ││
                    │  Writer: connectors          │↓
                    │                    listing_detail                │
                    └────────────────────────────┬────────────────────┘
                                                 │
                    ┌────────────────────────────┐│
                    │  STAGE 2: ENRICH            ││
                    │  (hourly LLM batch)         ││
                    │  Writer: enrichment job      │↓
                    │                 listing_enrichment               │
                    └────────────────────────────┬────────────────────┘
                                                 │
                    ┌────────────────────────────┐│
                    │  STAGE 3: SCORE             ││
                    │  (post-enrichment)           ││
                    │  Writer: scoring job          │↓
                    │                    listing_score                 │
                    └─────────────────────────────────────────────────┘
```

**Relationship to existing M2 opportunity score:** The M2 `compute_opportunity_score()` (0–100) remains as-is for ranking and tiered alerting (what surfaces to the dashboard). The new composite scores layer alongside it for the buy/action decision (how much will I make, is it worth my capital, is it safe).

---

## 1. New Tables Schema

### 1.1 `listing_detail` — Raw data from detail page fetches

Written by connectors during the selective 2nd-pass fetch. One row per listing observation.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `detail_id` | BigInteger PK | no | autoincrement |
| `obs_id` | BigInteger FK → listing_observation.obs_id | no | unique |
| `description` | Text | yes | Full listing description text |
| `description_length` | Integer | yes | Character count, computed on insert |
| `photo_urls` | ARRAY(Text) | yes | Photo URLs from listing |
| `photo_count` | Integer | yes | Length of photo_urls, denormalized |
| `local_pickup_only` | Boolean | yes | True if no shipping / "remise en main propre" |
| `negotiation_enabled` | Boolean | yes | True if offer button present |
| `original_posted_at` | TIMESTAMP with TZ | yes | When listing was first published on platform |
| `seller_account_age_days` | Integer | yes | Days since seller account creation |
| `seller_transaction_count` | Integer | yes | Seller's completed sales count |
| `view_count` | Integer | yes | Listing view/impression count |
| `favorite_count` | Integer | yes | Likes / watchers count |
| `fetched_at` | TIMESTAMP with TZ | no | When this detail was fetched |

**Indexes:** unique on `(obs_id)`, btree on `(fetched_at)`.

**Design notes:**
- `original_posted_at` enables computing `days_on_market` without storing a derived field that goes stale.
- `view_count` / `favorite_count` are demand signals available on Vinted and LeBonCoin.
- `seller_account_age_days` and `seller_transaction_count` strengthen seller trust beyond the current `seller_rating` field.
- `description_length` is denormalized to avoid `length()` calls in queries — computed once on insert.

### 1.2 `listing_enrichment` — LLM-derived analysis

Written by the hourly LLM batch job. One row per analyzed listing observation.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `enrichment_id` | BigInteger PK | no | autoincrement |
| `obs_id` | BigInteger FK → listing_observation.obs_id | no | unique |
| `urgency_score` | Numeric(3,2) | yes | 0.00–1.00, LLM-assessed seller urgency (synthesizes keywords, tone, pricing, DOM) |
| `urgency_keywords` | ARRAY(Text) | yes | Detected keywords for auditability: ["déménagement", "urgent", ...] |
| `has_original_box` | Boolean | yes | LLM-inferred from description and/or photos |
| `has_receipt_or_invoice` | Boolean | yes | LLM-inferred from description |
| `accessories_included` | ARRAY(Text) | yes | e.g., ["charger", "cable", "manual"] |
| `accessories_completeness` | Numeric(3,2) | yes | 0.00–1.00, fraction of expected accessories present |
| `photo_quality_score` | Numeric(3,2) | yes | 0.00–1.00, LLM vision assessment |
| `listing_quality_score` | Numeric(3,2) | yes | 0.00–1.00, composite of description + photo + completeness quality |
| `condition_confidence` | Numeric(3,2) | yes | 0.00–1.00, trust in stated condition |
| `fakeness_probability` | Numeric(3,2) | yes | 0.00–1.00, counterfeit risk |
| `seller_motivation_score` | Numeric(3,2) | yes | 0.00–1.00, composite: urgency + DOM + price positioning |
| `llm_model` | Text | yes | Model used, e.g., "gemini-2.0-flash" |
| `llm_raw_response` | JSONB | yes | Full LLM response for audit/debugging |
| `enriched_at` | TIMESTAMP with TZ | no | When enrichment was performed |
| `cost_tokens` | Integer | yes | Token usage for this enrichment call |

**Indexes:** unique on `(obs_id)`, btree on `(enriched_at)`.

**Design notes:**
- `urgency_score` is the actionable signal; `urgency_keywords` is the audit trail showing why.
- `seller_motivation_score` combines urgency, days on market, and pricing relative to PMN into a single "how desperate is this seller?" signal.
- `listing_quality_score` is an inverse alpha signal — bad listings for retail buyers = good opportunities for arbitrage.
- `condition_confidence` lets scoring discount listings where condition claims seem unreliable.
- `fakeness_probability` feeds directly into `risk_adjusted_confidence`.

### 1.3 `listing_score` — Materialized composite action scores

Written by the scoring job after each enrichment batch. One row per scored listing observation.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `score_id` | BigInteger PK | no | autoincrement |
| `obs_id` | BigInteger FK → listing_observation.obs_id | no | unique |
| `product_id` | UUID FK → product_template.product_id | no | denormalized for fast queries |
| `arbitrage_spread_eur` | Numeric | yes | True profit: (est. sale − fees − shipping) − acquisition cost |
| `net_roi_pct` | Numeric | yes | spread / acquisition_cost × 100 |
| `risk_adjusted_confidence` | Numeric(5,2) | yes | 0–100, weighted composite |
| `acquisition_cost_eur` | Numeric | yes | Price + buyer shipping + buyer platform fees |
| `estimated_sale_price_eur` | Numeric | yes | PMN adjusted for condition and completeness |
| `estimated_sell_fees_eur` | Numeric | yes | Target platform commission + payment fees |
| `estimated_sell_shipping_eur` | Numeric | yes | Estimated outbound shipping |
| `days_on_market` | Integer | yes | now() − original_posted_at (or first observed_at) |
| `score_breakdown` | JSONB | yes | Factor-by-factor breakdown for dashboard transparency |
| `scored_at` | TIMESTAMP with TZ | no | When this score was computed |

**Indexes:** unique on `(obs_id)`, btree on `(product_id, risk_adjusted_confidence DESC)`, btree on `(product_id, arbitrage_spread_eur DESC)`.

**Design notes:**
- `acquisition_cost_eur` and `estimated_sale_price_eur` make the spread fully auditable.
- `score_breakdown` JSONB stores individual factor values for dashboard "why this score?" display.
- Compound indexes on `(product_id, score DESC)` enable fast "top opportunities for product X" queries.
- `days_on_market` is materialized here because it changes daily; recomputed on each scoring run.

---

## 2. Dataclass Contracts

### 2.1 `ListingDetail` — Connector detail fetch output

```python
class ListingDetail(BaseModel):
    obs_id: int
    description: str | None
    photo_urls: list[str]
    local_pickup_only: bool | None
    negotiation_enabled: bool | None
    original_posted_at: datetime | None
    seller_account_age_days: int | None
    seller_transaction_count: int | None
    view_count: int | None
    favorite_count: int | None
```

Each connector implements `fetch_detail(listing_id: str) -> ListingDetail | None`. Fields that can't be extracted return None.

### 2.2 Per-connector field availability

| Field | eBay Browse API | LBC API | LBC Scrape | Vinted API | Vinted Scrape |
|-------|-----------------|---------|------------|------------|---------------|
| description | ✓ | ✓ (already extracted, not stored) | ✓ | ✓ (item endpoint) | ✓ |
| photo_urls | ✓ | ✓ | ✓ | ✓ | ✓ |
| local_pickup_only | ✓ (shipping options) | ✓ (shipping field) | ✓ | ✗ (always shipped) | ✗ |
| negotiation_enabled | ✓ (bestOfferEnabled) | ✓ (negotiation flag) | ~ (heuristic) | ✓ (offer button) | ✓ |
| original_posted_at | ✓ (startTime) | ✓ (first_publication_date) | ✓ | ✓ (created_at) | ~ |
| seller_account_age_days | ✓ | ~ (profile scrape) | ~ | ✓ | ~ |
| seller_transaction_count | ✓ (feedbackScore) | ~ | ~ | ✓ | ~ |
| view_count | ✓ (viewCount) | ✗ | ✗ | ✓ | ✓ |
| favorite_count | ✓ (watchCount) | ✗ | ✗ | ✓ (favourite_count) | ✓ |

**Legend:** ✓ = available, ~ = possible but unreliable/extra effort, ✗ = not available.

---

## 3. Pipeline Design

### 3.1 Stage 1: Detail Fetch (selective 2nd-pass)

**Trigger:** Runs inline after existing ingestion filtering, within the same ingestion job.

**Candidate selection logic:**

```
filtered_listings (passed price/brand/word filters)
        ↓
┌─── PMN exists for product? ───┐
│                                │
YES                              NO
│                                │
price < PMN × 1.1?         price_min/price_max defined?
│                                │
YES → fetch detail          YES → fetch detail (already passed price filter)
NO  → skip                  NO  → fetch detail for ALL (cold start, no signal to filter on)
```

**Cold start handling:** When no PMN exists AND no `price_min`/`price_max` on the product template, ALL filtered listings get a detail fetch. This ensures the system bootstraps data collection. Once PMN is established (typically after first ingestion + computation cycle), the selective filter kicks in.

**Rate limiting:** Detail fetches are throttled per platform (configurable delay between requests) to avoid anti-bot detection. Default: 2s between fetches for Vinted, 1s for LBC, 0.5s for eBay (API is more tolerant).

**Persistence:** Each `ListingDetail` is written to the `listing_detail` table via upsert on `obs_id`. If a detail row already exists (re-ingested listing), it is updated with fresh data (view_count, favorite_count may have changed).

### 3.2 Stage 2: Enrichment (hourly LLM batch)

**Trigger:** ARQ cron job, runs every hour.

**Listing selection:**
1. Has a `listing_detail` row (detail was fetched)
2. Does NOT yet have a `listing_enrichment` row
3. OR has enrichment older than 7 days AND listing is still active (`is_stale = False`)

**Prioritization:** Un-enriched listings with lowest `price / PMN` ratio first (highest opportunity signal). Cold start: when no PMN, prioritize by lowest absolute price.

**Single LLM call per listing.** Structured prompt with:

**Input:**
- Listing: title, description, condition_raw, price, currency
- Product context: category, brand, PMN (if available), expected accessories for category
- Photos: photo_urls passed as images (if vision model supports it)
- Temporal: original_posted_at, days since posted

**Output (structured JSON):**
```json
{
  "urgency_score": 0.85,
  "urgency_keywords": ["déménagement", "doit partir"],
  "has_original_box": true,
  "has_receipt_or_invoice": false,
  "accessories_included": ["charger", "cable"],
  "accessories_completeness": 0.67,
  "photo_quality_score": 0.4,
  "listing_quality_score": 0.45,
  "condition_confidence": 0.8,
  "fakeness_probability": 0.1,
  "seller_motivation_score": 0.75
}
```

**Batch config:**
- Max listings per run: configurable, default 50
- LLM model: Gemini Flash (cost ~€0.01/listing with vision)
- Budget cap: configurable max tokens/day to control costs
- Re-enrichment: active listings re-enriched after 7 days (seller_motivation_score evolves with DOM)

### 3.3 Stage 3: Score (post-enrichment)

**Trigger:** Runs immediately after enrichment batch completes. Also runs when:
- PMN changes for a product (recompute all active listings for that product)
- Active listings cross DOM thresholds (7d, 14d, 30d, 45d)

**Composite score formulas:**

#### `acquisition_cost_eur`
```
listing.price
+ listing.shipping_cost (0 if local_pickup_only or null)
+ buyer_platform_fees (Vinted: ~5% buyer protection; eBay/LBC: 0 for buyer)
```

#### `estimated_sale_price_eur`
```
PMN
× condition_adjustment:
    new     = 1.10
    like_new = 1.00
    good    = 0.90
    fair    = 0.75
× completeness_adjustment:
    has_original_box:     +5%
    has_receipt_or_invoice: +5%
    full accessories:     +5%
    (multiplicative: 1.0 × 1.05 × 1.05 × 1.05 max)
```

#### `estimated_sell_fees_eur`
```
estimated_sale_price × platform_fee_rate
    ebay:      12.9% commission + 3.0% payment = 15.9%
    leboncoin: 5.0% commission + 3.0% payment = 8.0%
    vinted:    5.0% commission + 3.0% payment = 8.0%
```

#### `estimated_sell_shipping_eur`
Flat estimate by product category (configurable):
```
electronics = €8
watches     = €6
clothing    = €5
default     = €7
```

#### `arbitrage_spread_eur`
```
estimated_sale_price_eur - estimated_sell_fees_eur - estimated_sell_shipping_eur - acquisition_cost_eur
```

#### `net_roi_pct`
```
(arbitrage_spread_eur / acquisition_cost_eur) × 100
```

#### `risk_adjusted_confidence` (0–100)

| Factor | Weight | Source | Computation |
|--------|--------|--------|-------------|
| Seller trust | 20% | seller_rating + account_age + transaction_count | Normalized 0–1: avg of available sub-signals |
| Fakeness inverse | 25% | listing_enrichment.fakeness_probability | 1 − fakeness_probability |
| Condition confidence | 15% | listing_enrichment.condition_confidence | Direct 0–1 value |
| PMN confidence | 20% | market_price_normal.confidence | Direct 0–1 value |
| Price volatility inverse | 10% | product_daily_metrics.price_std / PMN | 1 − (price_std / pmn), clamped [0, 1] |
| Listing quality | 10% | listing_enrichment.listing_quality_score | Direct 0–1 value |

**Final:** `weighted_sum × 100`. Listings below 80 are excluded from the default dashboard view.

**Neutral defaults (when enrichment data is missing):**
- All enrichment-sourced factors default to 0.5 (neutral)
- PMN confidence uses actual value from `market_price_normal` (available before enrichment)
- If no PMN at all: `risk_adjusted_confidence` capped at 40 (low confidence, still visible in "all listings" view)

**Score breakdown JSONB** stores each factor's raw value and weighted contribution for dashboard transparency.

---

## 4. Quality Testing Strategy

### 4.1 Real Data Connector Tests

Each connector gets integration tests that run against **live marketplace APIs/pages**. No mocks.

**Per-connector detail fetch tests:**
- Fetch a known active listing by ID
- Assert all fields the platform is expected to provide (per Section 2.2 table) are non-null
- Assert type correctness: `photo_count > 0`, `description_length > 0`, `original_posted_at < now()`
- Assert value ranges: `view_count >= 0`, `seller_transaction_count >= 0`, `0 < price < 100000`
- For LBC: assert `description` is populated (currently extracted but discarded)
- For Vinted: assert `favorite_count` is returned from item endpoint

**Cross-connector consistency tests:**
- Search the same product across all 3 platforms
- Assert consistent `Listing` + `ListingDetail` structure
- Flag any connector returning significantly fewer non-null fields than expected

**Regression markers:**
- Tests tagged with expected field availability per platform
- If a platform changes API/DOM and a previously-available field starts returning null → test fails loudly, not silent degradation

### 4.2 Enrichment Quality Tests

**Structural validation (every enrichment run):**
- All scores in [0.0, 1.0] range
- `accessories_included` items are non-empty strings
- `accessories_completeness` ≤ 1.0 and consistent with array length vs. expected count for category
- `photo_quality_score` is null only when no `photo_urls` were available
- `llm_raw_response` is valid JSON and contains all expected keys

**Semantic validation (golden set):**
- Maintain ~20 real listings with human-labeled ground truth (labeled once by operator)
- Run enrichment on golden set periodically (weekly or on prompt changes)
- Assertions:
  - `has_original_box` matches ground truth ≥ 90% of the time
  - `has_receipt_or_invoice` matches ground truth ≥ 90% of the time
  - `urgency_score > 0.7` for known-urgent listings
  - `urgency_score < 0.3` for known-non-urgent listings
  - `fakeness_probability < 0.3` for known-genuine items
  - `photo_quality_score` rank-order correlation ≥ 0.8 with human ranking
- If accuracy drops → enrichment prompt needs tuning, test fails

### 4.3 Score Quality Tests

**Arithmetic correctness:**
- `arbitrage_spread_eur == estimated_sale_price_eur - estimated_sell_fees_eur - estimated_sell_shipping_eur - acquisition_cost_eur` (exact match within float tolerance)
- `net_roi_pct == (arbitrage_spread_eur / acquisition_cost_eur) × 100`
- `risk_adjusted_confidence` factor weights sum to 1.0

**Business logic validation (on real data):**
- Listing priced at 50% of PMN + good condition + high seller trust → `risk_adjusted_confidence > 80`
- Listing priced above PMN → `arbitrage_spread_eur < 0`
- Listing with `fakeness_probability > 0.8` → `risk_adjusted_confidence < 50`
- `acquisition_cost_eur >= listing.price` (costs only add, never reduce)

**Cold start tests:**
- Product with no PMN → scores use `price_min`/`price_max` fallback, `risk_adjusted_confidence` capped at 40
- Product with PMN but no enrichment → enrichment factors at neutral 0.5 defaults
- Neither PMN nor enrichment → scored with both fallbacks, `risk_adjusted_confidence` capped at 40

### 4.4 Data Freshness Monitoring

Integrated into existing health checks (`make health`):

| Metric | Threshold | Action |
|--------|-----------|--------|
| `listing_detail` coverage | ≥ 80% of filtered listings within 1h of ingestion | Warn |
| `listing_enrichment` coverage | ≥ 90% of detail rows within 2h | Warn |
| `listing_score` coverage | 100% of enriched rows within 15min of enrichment batch | Alert |
| Enrichment batch duration | < 30 minutes | Warn if exceeded |
| Score computation duration | < 5 minutes | Warn if exceeded |

Threshold breaches logged and optionally trigger Telegram alert via existing alert infrastructure.

---

## 5. Phased Implementation

### Phase 1: Foundation (schema + raw storage)
- Alembic migration: create `listing_detail`, `listing_enrichment`, `listing_score` tables
- `ListingDetail` dataclass in `libs/common/models.py`
- ORM models for all three new tables
- Store `description` from LBC API (already extracted, just needs persisting)
- Implement `fetch_detail()` on each connector (start with fields available from existing APIs)
- 2nd-pass detail fetch logic with PMN-based candidate selection and cold-start fallback
- Real data integration tests for detail fetch per connector

### Phase 2: Enrichment pipeline
- Hourly ARQ cron job: query un-enriched listings, call LLM, persist to `listing_enrichment`
- Structured LLM prompt design (single call per listing, all enrichment fields)
- Budget cap and prioritization logic (lowest price/PMN ratio first)
- Golden set creation (operator labels ~20 real listings)
- Enrichment structural + semantic quality tests

### Phase 3: Scoring engine
- Scoring module: compute all composite scores from observation + detail + enrichment + PMN
- Post-enrichment trigger: score all newly enriched listings
- PMN-change trigger: rescore affected listings
- DOM-threshold trigger: rescore at 7d, 14d, 30d, 45d
- `listing_score` persistence with full breakdown
- Score arithmetic, business logic, and cold-start tests

### Phase 4: Dashboard integration
- Backend API endpoints serving scored listings (join across 4 tables)
- Default filter: `risk_adjusted_confidence >= 80`
- Sort options: `arbitrage_spread_eur` DESC, `net_roi_pct` DESC
- Score breakdown display for transparency ("why this score?")
- Freshness monitoring in `make health`

---

## 6. Relationship to Other Specs

| Spec | Relationship |
|------|-------------|
| **M2: Fast & Precise** (2026-03-16) | M2 opportunity score (0–100) stays for ranking/alerting. New composite scores layer alongside for buy decisions. M2 ingestion frequency changes (hourly) feed this pipeline. |
| **Connector Data Quality Audit** (2026-03-17) | Separate workstream. Fixes to connector quality (real seller ratings, reliable shipping costs, sold signals) improve the inputs to this data model. This spec defines the target interface contracts connectors must satisfy. |
| **Roadmap** (2026-03-14) | This spec spans Milestone 2 (scoring) and Milestone 3 (product discovery, LLM activation). Phase 1–2 are M2 scope, Phase 3–4 bridge into M3. |

---

## 7. Cost Estimate

| Component | Cost | Frequency |
|-----------|------|-----------|
| Detail page fetches | Free (API calls) / compute only | Per ingestion run |
| LLM enrichment (Gemini Flash + vision) | ~€0.01/listing | Hourly batch, ~50 listings/run |
| Monthly enrichment budget (50/hr × 24h × 30d) | ~€360/month at full capacity | Configurable cap |
| Score computation | CPU only, negligible | After each enrichment batch |

Budget cap is configurable. Start conservative (20 listings/run ≈ €144/month) and scale up as ROI is validated.

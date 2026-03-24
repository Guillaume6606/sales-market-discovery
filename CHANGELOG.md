# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Enriched data model** — Three new tables (`listing_detail`, `listing_enrichment`, `listing_score`) for capturing logistics, seller psychology, temporal signals, and product completeness
- **Selective detail fetch** — 2nd-pass detail page fetching for promising listings with cold-start fallback
- **LLM enrichment pipeline** — Hourly batch job analyzing listings via Gemini Flash for urgency, completeness, photo quality, fakeness, and seller motivation
- **Composite action scores** — `arbitrage_spread_eur`, `net_roi_pct`, `risk_adjusted_confidence` materialized after each enrichment batch
- **Shared condition normalization** — Extracted from 5 connector files into `libs/common/condition.py`
- **Scored listings API** — `/products/{id}/scored-listings` endpoint with sort and confidence filter
- **Enrichment health monitoring** — `/health/enrichment` endpoint tracking pipeline freshness

### Changed
- Connectors now use shared `normalize_condition()` instead of per-connector implementations

---

- **Enrichment batch job** (`ingestion/enrichment.py`): hourly ARQ cron job that queries listings with detail but no enrichment (or stale enrichment), calls Gemini Flash LLM via `get_genai_client()`, and upserts results into `listing_enrichment`; supports both fresh and re-enrichment buckets with configurable batch sizes
- **Enrichment smoke tests** (`tests/smoke/test_07_enrichment.py`): 12 structural tests for `parse_enrichment_response` (score clamping, missing keys, markdown fences, null booleans) and a skippable golden-set accuracy test class; all structural tests run without network access
- Registered `run_enrichment_batch` in `WorkerSettings.functions` and `WorkerSettings.cron_jobs` (every hour at :30)


- **Multi-page Streamlit UI**: converted monolithic 1041-line `ui/app.py` into 6-page multi-page app with auto sidebar navigation
- **Health & Observability page** (`5_Health.py`): system status banner, per-connector health cards, stale product warnings, ingestion run history with pagination, PMN computation status, aggregate PMN accuracy (worst/best products), connector audit quality
- **Alert Management page** (`6_Alerts.py`): alert precision dashboard, alert rules CRUD, rule testing, alert events timeline with pagination, one-click feedback buttons (interested/not interested/purchased)
- **Shared UI library** (`ui/lib/`): centralized API client with Docker/localhost auto-fallback, 20+ fetch functions covering all backend endpoints, display formatters (confidence badges, relative times, discount formatting)
- PMN confidence column in Discovery table (ProgressColumn)
- PMN accuracy section in product detail panel (hit rate, MAE, matched sales count)
- Pagination on Discovery, Listing Explorer, and Alert Events pages
- `st.dataframe` with `column_config` for Discovery table (replaces row-by-row rendering)
- Loading spinners on all data fetches
- Cross-page navigation from Listing Explorer to Discovery (select product → view in Discovery)
- LLM validation badge and filter toggle in Listing Explorer
- Smart column toggle in Listing Explorer (default 7 columns, toggle for all 12)
- `words_to_avoid` and `enable_llm_validation` fields in Product Setup form
- `st.number_input` for price fields in Product Setup (replaces text inputs)
- Per-product ingestion history expander in Product Setup
- Job ID display and status check in Import Data page
- Queue status section in Import Data page

### Changed
- **UI architecture**: `ui/app.py` stripped to ~24-line entry point; all page logic moved to `ui/pages/` directory
- **API URL resolution**: auto-probes Docker hostname then falls back to localhost, works in both Docker and local dev without manual config
- **Discovery filters**: "Delta vs PMN" slider (negative %) replaced with "Minimum Discount %" (positive = better deal)
- **Listing Explorer `is_sold`**: now sends Python booleans instead of string `"false"`/`"true"`

### Removed
- Monolithic tab-based navigation in `ui/app.py`
- Row-by-row rendering loop in Discovery tab

---

- URL slug title fallback for Vinted connector — extracts title from `/items/{id}-{slug}` when CSS selectors fail
- Per-connector field exclusions in audit (`CONNECTOR_FIELD_EXCLUSIONS`) for known API limitations (e.g. LeBonCoin condition)
- Verifiable listing count in audit reports to distinguish auditable vs unverifiable listings
- Timestamp filter on audit DB query to scope to freshly-ingested listings only
- Consent banner dismissal (Accepter / OneTrust) in audit page capture
- Browser stealth fingerprint test script (`test-stealth-config.py`)
- Vinted stealth bypass plan (`docs/plan-vinted-stealth-bypass.md`)

### Changed
- **LLM service**: migrated from LangChain + langchain-google-genai to unified `google-genai` SDK with Vertex AI and Application Default Credentials
- **Audit page capture**: switched from fixed 2s sleep to `networkidle` wait (8s timeout, 3s fallback) for better JS hydration
- **Ingestion dispatch**: `run_full_ingestion()` now only calls sub-sources explicitly present in `limits` dict (prevents audit from running unwanted sources like `leboncoin_sold`)
- **Start script**: uses `uv run` and `patchright` instead of raw `python` and `playwright`
- **Dockerfile**: `patchright install chromium` instead of `playwright install` + `patchright install chrome`; ensure `/home/pwuser/.cache/uv` directory exists

### Fixed
- LeBonCoin audit accuracy: 50% -> 91.7% by removing `leboncoin_sold` from audit source map (active listings wrongly marked `is_sold=True`)
- `db.expunge()` failures in worker audit: replaced with `make_transient()` to avoid lazy-load issues
- Loguru format strings in audit capture (`%s` -> `{}`)
- Removed invalid `channel="chrome"` arg from Playwright persistent context

### Removed
- `langchain`, `langchain-google-genai`, `google-generativeai` dependencies (replaced by `google-genai`)

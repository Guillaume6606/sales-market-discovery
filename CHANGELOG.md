# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
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

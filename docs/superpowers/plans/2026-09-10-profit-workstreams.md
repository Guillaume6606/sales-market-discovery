# Four profit workstreams implementation plan

> Execute with the subagent-driven-development and test-driven-development skills. The user approved all four workstreams in the preceding review.

**Goal:** Deliver a private operator workflow from defensible valuation through reliable alerts to realized trade P&L.
**Spec:** `docs/2026-09-10-code-review-profit-roadmap.md`.
**Architecture:** Keep PostgreSQL/FastAPI/ARQ/Streamlit. Add explicit verified valuation records and a separate trade ledger; use one eligibility gate for alerts. Keep all changes on `feature/profit-workstreams`.

## Constraints and decisions

- No deployment, marketplace purchase, customer messages, or destructive production cleanup. Supply an inspectable quarantine command rather than altering live data.
- EUR only for actionable valuation and ledger; reject unsupported currencies instead of silently converting.
- Fees/social charges are explicit operator inputs, not implied legal advice or guessed defaults.
- Manual verified references provide the first safe valuation source. Asking-price PMN remains descriptive and cannot authorize alerts.
- Unknown identity, condition, evidence, fees or acquisition cost blocks an actionable recommendation.
- Existing review/strategy documents are preserved. Existing failed tests are in scope to repair.
- Root owns shared `models.py`, main router registration, settings, ingestion/worker, alert engine and changelog. Agents use separate model modules sharing Base and independent migration revisions, coordinated below.

## Workstream 1: Stop misleading decisions (root)

- [x] Write regressions for disabled LBC sold ingestion, missing/unverified PMN, stale listings, discount signs and negative feedback profit; observe failures.
- [x] Disable unsupported sold sources, keep legacy PMN explicitly unverified, block unsafe alerts, fix UI unit/sign conventions and allow signed profit.
- [x] Preserve first-seen time and observation revisions; supply dry-run-first quarantine tooling for historical LBC sold contamination.
- [x] Run focused tests; commit the complete cross-workstream unit after final review.

## Workstream 2: Verified valuation (valuation agent)

- [x] Add reference record schema/migration `0009_verified_valuation` after root migration `0008_profit_foundations`: exact required/excluded tokens, normalized condition, reviewed comparable price, evidence link/date/reviewer/expiry, destination, explicit fee/tax/logistics/risk/min-profit inputs.
- [x] Add `evaluate_valuation(db, observation, now=None) -> dict` in `ingestion/valuation.py`; return eligible, reasons, reference_id, estimated_sale_price_eur, acquisition_cost_eur, contribution_eur, max_buy_price_eur. Reject missing/stale/mismatched inputs; no box/receipt uplift.
- [x] Expose reference CRUD and listing valuation APIs through standalone router and an operator Streamlit page.
- [x] Test financial arithmetic, exact matching, expired evidence, currency, negative contribution and API validation before implementation; run tests and report interface.

## Workstream 3: Reliable pipeline (root + test-infrastructure agent)

- [x] Test and wire detail fetching, computation, score refresh, verified valuation eligibility, pending alert delivery and bounded retry with unique event keys.
- [x] Make per-product/source scheduling interval-based with overlap control and incremental fresh inputs; propagate partial/error outcomes.
- [x] Track sent/failed/pending delivery, exclude sold/stale records and revalidate before retry.
- [x] Test-infrastructure agent replaces SQLite metadata mutation with isolated PostgreSQL integration fixtures, fixes network unit fixture, builds ingestion in CI, checks readiness/migrations. No shared production changes by that agent without coordination.
- [x] Run combined unit, real PostgreSQL integration and migration tests; no live messages.

## Workstream 4: Inventory and realized P&L (ledger agent)

- [x] Add independent trade model/migration `0010_trade_ledger` after `0009_verified_valuation`; store acquisition, sale, payout dates, explicit actual costs, refunds, status, notes, time spent and optional observation link.
- [x] Expose ledger create/update/list and monthly summaries; account for signed losses, only recognize settled sale proceeds, expose unsold inventory and committed capital separately, record monthly overhead once.
- [x] Provide Streamlit page for purchase, settlement, cost editing, inventory and monthly P&L/export.
- [x] Test lifecycle/date/amount validation and realized-versus-unsold calculations before implementation. Run focused tests and report router registration.

## Interface review and completion

| Shared boundary | Producer → consumer | Resolution |
|---|---|---|
| Migration chain | root → valuation → ledger | 0008 → 0009 → 0010; independent files, no parallel edits |
| Base metadata | root models → standalone models | each agent imports Base; root registers modules before schema/migration usage |
| Alert valuation | valuation agent → root eligibility | evaluate_valuation contract above; root uses reasons for suppression and contribution for rules |
| Router registration | agents → root main.py | agents report router modules; root registers and adds Caddy routes |
| Verification | all code → infrastructure agent/root | PostgreSQL test DB opt-in; isolated schema; unit tests offline |

- [x] Complete independent final boundary review. Ruff, unit, integration, compilation and migration checks have passed.
- [x] Update README/CHANGELOG and this progress checklist with evidence and operational steps.
- [x] Commit completed work without merging or deploying; report remaining runtime prerequisites honestly. Implementation commit: `1306be0`.

## Verification evidence — 2026-09-10–11

- **Measured:** 323/323 unit and Streamlit AppTest cases passed in the existing local Python 3.14 environment. CI uses Python 3.13.
- **Measured:** 24/24 PostgreSQL integration cases passed on disposable PostgreSQL 16 with a generated schema per test. Marketplace and Telegram calls were mocked; valuation, SQL persistence, scoring, outbox retries, feedback losses, trade lifecycle, quarantine and price-history queries used real PostgreSQL.
- **Measured:** Ruff check and format check passed across 129 Python files; compilation and `git diff --check` passed. Ruff corrected the pre-existing formatting issue in `scripts/seed.py`.
- **Measured by the infrastructure review:** ingestion Docker image build passed (`smd-ingestion:test-infra`). Final blank → head → `0007_enrichment_tables` → head migration round trip passed. Final revision: `0010_trade_ledger`, with 19 application tables including all new valuation/review/ledger/event tables. A cached rebuild from the final source and imports of worker/valuation/trade modules passed as `pwuser`.
- **Measured:** Navigation/valuation empty-state smoke passed through Streamlit AppTest with mocked HTTP. Dedicated valuation and inventory page tests also exercise financial string rendering and operator forms.
- **Unknown:** production connector availability, deployed readiness, alert quality on unseen listings, and realized monthly profit. No live messages, purchases, deployment or production cleanup were performed.

## Handover

See `docs/profit-workstreams-operations.md` for migration, archive/quarantine, capital, reference setup, listing-input review, alert delivery semantics, ledger close semantics and the proposed measured resale pilot.

## Final review resolutions

- Ranking now evaluates fresh candidates before applying the result limit, so cached scores cannot hide a newly eligible listing.
- Failed or suppressed alerts have an explicit eligibility-checked retry action with preserved history; delivered alerts cannot be resent through it.
- Closing a previously sold trade preserves its recorded sale date.
- PostgreSQL and Streamlit regressions cover these cases. The final ingestion image `smd-ingestion:profit-workstreams` built and imported the worker, valuation and trade modules successfully.

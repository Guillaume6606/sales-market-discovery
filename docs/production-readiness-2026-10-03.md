# Release fixes pass; provider access still limits production readiness

The release preserves every current feature. Application reliability and extraction semantics were corrected, but LeBonCoin and Vinted access from the VPS remains an external blocker. Successful container startup does not establish successful scraping or marketplace-wide extraction accuracy.

## Branch assessment

Measured with `git diff --numstat master..fd2dda6`: the original branch changed 124 files, adding 12,961 lines and removing 1,104. Its additions comprise 7,253 runtime/config lines, 4,426 test lines and 1,282 documentation lines. It contains valuation, trade-ledger, delivery, VLM and local human-review work; retaining all those features necessarily retains a substantial diff.

The production corrections are isolated on `fix/production-readiness`, based on the complete feature branch. The original checkout and its local edits were preserved. Measured through code commit `a9a9ee3`, the corrections add 451/remove 178 runtime/config lines and add 1,414/remove 42 test lines. This is reliability work with regression coverage, not a claim that the overall branch has become small. No additional service, retry framework, model cascade or scraping fallback was introduced. The redundant queued heartbeat was replaced by ARQ's existing heartbeat.

## Corrected behavior

- Frozen, non-editable image dependencies; direct runtime commands; writable worker reports. Deployment refuses quick updates, waits for dependencies, stops writers, backs up before migrations and checks readiness. Backend/UI use Python 3.13; the existing Playwright base uses Python 3.12 for ingestion.
- Search/detail denial cooldowns survive worker restarts; LeBonCoin pagination and SDK retries are bounded. Unknown shipping and transaction evidence remain unknown. Empty fetches are separated from successful persisted ingestion.
- eBay feedback percentages map consistently to a bounded score; feedback/listing counts do not become verified transaction counts. Migration `0013_seller_signal_semantics` clears the old proxies while preserving rows and prices. An old count in the 0–5 range is also cleared.
- Enforced VLM decisions require current details; stale or failed refreshes cannot qualify a listing. Shadow mode, budgets, cache, evaluation tooling and local human review remain available. Unauthenticated Telegram requests are rejected.
- ORM metadata includes the indexes already present in migrations. Smoke checks use installed Python and the current field/freshness contracts.

## Verification

Measured on the final code: **675 tests passed**, with six pre-existing deprecation warnings. This includes unit tests, isolated PostgreSQL integration tests and network-free enrichment/scoring smoke tests. Ruff and `git diff --check` passed. The full live-provider smoke suite was not declared passing: denied sources prevent that claim.

The local compose deployment built, migrated from an empty database and passed readiness, UI health, Chromium launch and report-write checks. Native VPS images were built separately. Measured on the final native images: 35 container, schema, enrichment and scoring smoke checks passed; this count overlaps the offline structural checks and is not added to 675. Source fingerprints inside the images matched the tested code commit.

A protected production dump was restored into a separate VPS compose project with generated test credentials, an isolated Redis/database and cron/notifications disabled. Migration rehearsal from `0010_trade_ledger` to `0013_seller_signal_semantics` preserved these measured counts:

| Table | Before | After |
|---|---:|---:|
| Listing observations | 4,462 | 4,462 |
| Listing details | 1,964 | 1,964 |
| Products | 6 | 6 |
| Alert events | 287 | 287 |
| Alert feedback | 0 | 0 |
| Trades | 0 | 0 |

Measured after migration: zero legacy eBay ratings and zero unsupported seller transaction counts remained. `alembic check` reported no pending operations. Staging readiness, discovery, valuation, trades, vision status and OpenAPI returned HTTP 200; an unsigned Telegram request was rejected. Chromium launched as the worker user and could write reports. All nine dashboard pages rendered through app navigation without exceptions or error banners; Caddy configuration validated.

The standalone human-review interface intentionally needs the private local benchmark corpus. That corpus was not uploaded. Its save/reveal/resume workflow passed the synthetic UI regression; fresh human-labeled VLM accuracy is **not measured** in this release.

## Extraction: payload fidelity, not real-world correctness

The deployed pre-change parser was replayed against the identical captured eBay payload as the candidate. The Sony WH-1000XM4 sample was used during development and exposed a rounding error; it is **not held out**. After fixing rounding, the Nintendo Switch OLED query was captured and evaluated once, without parser tuning against those results. Provider denials were not repeatedly retried to obtain a favorable sample.

Measured on the held-out public payload: eBay parsed 20/20 records. Conditional field precision/recall below uses only explicit, scorable source values. This measures preservation of provider data, not whether sellers are truthful, normalization is semantically correct, results match the target device, or all relevant marketplace listings were retrieved.

| eBay field | Baseline exact | Candidate exact | Candidate precision | Candidate recall |
|---|---:|---:|---:|---:|
| Title | 20/20 | 20/20 | 20/20 | 20/20 |
| Price | 20/20 | 20/20 | 20/20 | 20/20 |
| Currency | 20/20 | 20/20 | 20/20 | 20/20 |
| Raw condition | 20/20 | 20/20 | 20/20 | 20/20 |
| URL | 20/20 | 20/20 | 20/20 | 20/20 |
| Seller score from feedback percentage | 1/20 | 20/20 | 20/20 | 20/20 |
| Qualified France shipping cost | Unscorable | Unscorable | Unknown | Unknown |

No shipping quote in this sample explicitly qualified for France. The old parser asserted 20 unsupported shipping values; the candidate asserted none and retained 20 unknowns. This is conservative abstention, not measured shipping accuracy.

Measured CashConverters HTML slice: one product page supplied nine store offers; all nine parsed. Title, price, raw condition and URL each matched 9/9 source values (conditional precision/recall 9/9). Shipping had zero scorable references. These offers share one page and are correlated. No baseline comparison was available for this newly added provider.

These tiny, single-query slices do not support a population-level accuracy or superiority claim. No scan/image slice was evaluated. End-to-end relevance, marketplace retrieval recall and human-labeled VLM field accuracy remain unknown. A meaningful next evaluation requires a frozen, independently labeled, multi-product sample after provider access works; do not tune prompts on it.

Measured probe wall time, including capture and replay: eBay 8.769 seconds for 20 records and two HTTP requests; CashConverters 3.133 seconds for one page and three requests. These are not steady-state per-listing latency measurements. The probes made zero LLM calls, so inference tokens and inference cost were zero; provider/proxy/network cost was not measured.

## Provider availability

Measured baseline from production's rolling 24-hour ingestion history at the initial inspection: Vinted failed 132/132 runs, with no successes in the preceding seven days. LeBonCoin had 43 errors, 15 empty results and 74 successful runs out of 132; eBay had 112 successful and 23 empty runs out of 135. These are historical run outcomes, not field-quality scores.

Bounded VPS probes using the configured transport observed HTTP 200 home pages followed by HTTP 403 API responses for both LeBonCoin and Vinted. The proxy setting was present. Cooldowns and honest health reporting fix the repeated-failure behavior; they cannot grant access. An approved provider API/feed/account integration is still needed to restore reliable access. Quality for denied payloads remains unmeasured.

## Evidence and recovery

The rehearsal backup is `/opt/market-discovery/backups/readiness-rehearsal-20261003.dump` on the VPS, SHA-256 `66a64d41a994e228c57169c98f81442f65b1a343848fca9b12ce07921edff24f`. Restore was tested on the same VPS; production records and secrets were not exported to the development machine.

Public-source evidence and probe scripts are preserved with private permissions under `/home/debian/smd-candidate-20261003/evidence/` on the VPS:

- Probe SHA-256: `75dd51fe16b21beb592ff04ae61f2d236f3bb8d2ee14c7bd41715e40ade95840`.
- Held-out eBay payload: `652e89429a181702b7a6fd2ec23dbf59066acf3319551fb51a65ad4cb99dc6e7`.
- Held-out CashConverters payload: `5c278766717ce6fecb10d4afda7c5e65d40d2b24f57b1f6e2b4d8ac34674a734`.

Production code `a9a9ee3` was deployed on 2026-10-03 using the tested native images. Measured before/after the stopped-writer migration: 4,474 observations, 1,974 details, six products and 287 alerts were preserved; feedback/trades remained empty. Live readiness and UI health passed, all ten checked GET endpoints returned HTTP 200, and the unsigned webhook returned HTTP 401. Public HTTPS `/health` returned 200; dashboard, readiness, docs, trades and valuation routes returned 401 without credentials. Production vision remains disabled in shadow configuration, with no configuration warnings; no paid inference benchmark or synthetic notification was triggered.

The final recovery directory is `/opt/market-discovery/backups/release-20261003T091919Z`. It contains a custom database dump, previous source archive, row counts, checksums, schema/API validation and old image identities. Database SHA-256: `c25791f255cead19bcda68e831a37bb2adc41ee251d4a38e83f8e2735bbf2181`. Source archive SHA-256: `06607f666f574ed18111bbdbbf3ba1b2061a69ce66449babe7827320d2158301`. Previous backend, ingestion and UI images are retained as `smd-rollback-<service>:20261003T091919Z`. Migration 0013 intentionally does not recreate incorrect seller proxies on downgrade; recovery to the old release requires its saved source/images and pre-migration database backup, not an Alembic downgrade alone.

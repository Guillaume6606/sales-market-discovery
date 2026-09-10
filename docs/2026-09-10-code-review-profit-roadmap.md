# Reach recurring profit by fixing pricing truth and completing actual sales

Review date: 2026-09-10. Code reference: `c1d4f50`.

**Recommendation: keep this as a personal resale tool first. Repair the decision pipeline, operate a narrow product set, and measure realized profit. Consider subscriptions only after the sourcing advantage is demonstrated.**

**Estimated planning window: 3–6 months to a first €1,000 operating-profit month, conditional on adequate capital, roughly 10–20 hours/week, and profitable sourcing demonstrated in the pilot. This is not a measured forecast or a promise.**

**The code is reusable, but it is not yet a trustworthy autonomous buying signal. There is no verified current revenue, realized margin, production uptime, or out-of-sample pricing accuracy in this review.**

## 1. Target, scope, and evidence

The user clarified that the goal is $1,000/month in mixed profit, primarily personal resale, with subscriptions optional. Resale income is not MRR; only subscription revenue is MRR. The common objective should be monthly operating profit and cash actually available.

The operational examples below use **€1,000/month** because the system and marketplaces use EUR. This is a planning buffer, not a dollar/euro equivalence. The latest ECB reference returned by this research was **measured: USD 1.1622 per EUR on 2026-09-04**, making $1,000 approximately **derived: €860.44** at that historical rate. Reconcile the USD target at the reporting-date rate. [ECB reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html).

“Operating profit” here means realized sale proceeds minus acquisition, marketplace/payment fees, shipping, repairs, refunds/losses, applicable modeled turnover-based social contributions, and shared operating costs. It is **before personal income tax and before paying the owner for their time**. VAT status, income-tax treatment, and other business charges remain user-specific and unknown. Do not describe this as €1,000 take-home pay.

Review work covered the architecture, active connector paths, persistence and migrations, pricing/liquidity, filtering, enrichment, scoring, alert delivery, feedback APIs, dashboard flows, deployment/backup configuration, CI, and existing plans. Critical paths were read directly and call sites searched; this is a repository-wide engineering review, not a claim that every line or every live behavior has been exhaustively verified.

No production access, live purchases, paid API calls, deployment, or customer messages were performed. Production data contamination is a risk established from reachable code, not a measured count of affected rows. Historical documents were treated as hypotheses and checked against current source. The pre-existing untracked August strategy was left untouched. No existing graphify graph was available; findings below are grounded in source inspection.

### Verification performed

| Check | Measured result | Interpretation |
|---|---|---|
| Unit tests | 234 passed / 243 collected; 9 setup errors | LeBonCoin parser fixtures instantiate a client that makes a real network request; DNS resolution failed in the restricted environment. This does not establish a production connector outage. |
| Integration tests | 4 setup errors / 4 tests | SQLite fixture cannot create PostgreSQL JSONB columns. Integration behavior was not exercised. |
| Ruff lint | Passed | Static lint is clean. |
| Ruff formatting | 101 files formatted; 1 would change | `scripts/seed.py` fails the format gate. No formatting changes were made. |
| Repository inventory | 102 tracked Python files | Inventory count, not a coverage measurement. |
| Local runtime | Existing virtual environment uses Python 3.14 | CI requests 3.13; project permits >=3.11. Results are for the existing local environment, not a frozen reproduction of CI. |
| Production/smoke/E2E | Not run | Live reliability, credentials, alert delivery, data quality and backup restoration remain unmeasured. |

Commands used: `UV_CACHE_DIR=/private/tmp/smd-review-uv-cache uv run --offline --no-sync pytest tests/unit/ -q`; corresponding integration run with `--tb=line`; `ruff check . --output-format concise`; `ruff format --check .`. The separate integration output is in `/private/tmp/smd-review-integration.txt`.

## 2. Keep the architecture; repair the contracts between components

FastAPI, PostgreSQL, ARQ/Redis, Streamlit, and shared Python models are sufficient for the immediate objective. Keep the seed script, migrations, run tracking, normalized connector interface, pure calculation functions, Telegram delivery, and dashboard. Replacing Streamlit or introducing microservices would delay the first profitable sale without fixing the underlying problem.

The intended flow is:

`fetch → validate identity/provenance → persist → fetch detail → estimate exit value → calculate net profit → apply eligibility rules → deliver → record purchase/resale outcome`

The actual implementation is fragmented: daily ingestion can alert using existing PMN; detail fetching has no application caller; enrichment and scoring run separately; alerts do not use stored composite scores. The primary engineering work is joining these pieces into one observable, testable decision flow.

### Prioritized findings

P0 means fix before relying on purchase recommendations. P1 means fix before unattended operation or significant capital deployment. P2 means address as the system proves useful. Estimates are engineering estimates, not measured duration; overlapping fixes are consolidated in the roadmap rather than summed twice.

| Priority | Finding and evidence | Consequence | Required action |
|---|---|---|---|
| P0 | `ingestion/connectors/leboncoin_api.py:412` returns active listings from the sold function; `ingestion/ingestion.py:491` persists them with `force_is_sold=True`; `worker.py:108` schedules both paths. | Asking prices become sold evidence. The same observation may toggle active/sold. PMN and liquidity can become misleading. Bias direction and magnitude are unmeasured. | Disable this sold path, inspect affected rows, preserve an audit copy, correct provenance, invalidate affected PMN/history/scores, and recompute from defensible evidence. Never blindly delete the database. |
| P0 | `ingestion/connectors/ebay.py:112` always returns an empty sold result. `computation.py:69` falls back to active prices and gives them the same confidence formula. | No implemented verified market-wide transaction feed; a high confidence value can describe consistent asking prices rather than resale realizability. | Introduce explicit evidence types and reference timestamps. Bootstrap with manually verified comparable sales; block automated profit claims where no usable evidence exists. |
| P0 | `ProductTemplate` in `libs/common/models.py:40` has a search string and price bounds but no exact variant identity. `_matches_brand()` trusts search results whenever the brand is in the query. | Accessories, different generations, capacity variants, bundles, or damaged units can enter a shared reference distribution. Purchase-price filters also truncate the reference sample. | Separate acquisition filters from comparable-sale selection. Add required model/reference tokens, variant fields, exclusions, and human review for ambiguous matches. |
| P0 | `composite_scoring.py:297` computes selling fees from the **purchase** source. The PMN is product-wide; condition/box/receipt premiums are fixed multipliers. | Cross-platform net profit is not actually modeled. Condition can be adjusted twice, and accessory premiums may overstate value. | Explicit buy marketplace, sell marketplace, condition-matched reference, effective-dated fee profile, logistics, repair allowance and loss allowance. Use conservative supported estimates, not unvalidated premiums. |
| P0 | `run_full_ingestion()` alerts on price below existing PMN; `alert_engine.py` never reads `ListingScore`. Its ingestion query also omits the stale filter used by the worker's other alert path. | Enrichment and confidence thresholds displayed in the dashboard do not protect Telegram alerts. Stale opportunities can pass. | One authoritative alert eligibility service: active, fresh, exact match, acceptable evidence, positive net contribution, sufficient margin, available capital, and deduplicated delivery. |
| P1 | `ingestion/detail_fetch.py:102` defines orchestration, but repository search finds no caller outside that module. Enrichment requires an inner join to detail rows. | Fresh ingestion does not populate the prerequisite for enrichment; the apparent pipeline is incomplete. | Wire detail collection for selected candidates, with a persisted job state and failures. Make one end-to-end test prove the full path. |
| P1 | `worker.py:782` schedules marketplaces once daily; enrichment is hourly at :30 and scoring at :45. | This cannot support a short listing-to-alert latency target. A source can fail between daily runs without timely detection. | Configurable per-source/product scheduling, cursors, bounded concurrency, backoff and real freshness metrics. Start with a small priority set and increase only when measured request budgets permit it. |
| P1 | `ingestion/ingestion.py:128` overwrites observation time and price; eBay parsing sets observation time to now. | Old listings become apparently recent; price history is not immutable. Historical evaluation cannot reconstruct exactly what was known at decision time. | Separate first-seen, last-seen, marketplace-posted, verified-sold and fetched-at times; append price/status events. |
| P1 | `run_scoring_batch()` selects absent scores or scores older than enrichment, not scores invalidated by price, PMN, fee or detail changes. It does not exclude sold rows. | Stored spread can be stale or refer to unavailable inventory. Scored-listings API also only filters staleness. | Version score inputs and invalidate on any relevant change. Exclude sold/inactive products and expired evidence in both jobs and APIs. |
| P1 | `ui/pages/6_Alerts.py:212` submits a positive discount threshold; `alert_engine.py` compares a negative price-relative-to-PMN value. The UI liquidity input is 0–1 while computation returns 0–100. | A nominal 10% rule can admit a listing only 1% below PMN; liquidity filtering is much weaker than it appears. Seed rules use negative thresholds, so creation paths disagree. | Define one API convention, convert at the UI boundary, migrate existing rules deliberately, and add UI/API contract tests. |
| P1 | `backend/routers/feedback.py:41` rejects negative profit. Feedback has no purchase, sale, payout, cost or return lifecycle; the dashboard does not provide a complete profit ledger. | Losses cannot be recorded through the endpoint; monthly profit and cash cycle cannot be trusted. | Support signed profit immediately, then implement a minimal inventory/trade ledger including losses, refunds and unsold stock. |
| P1 | `_check_duplicate_alert()` treats every unsuppressed event as delivered, even when `send_opportunity_alert()` failed; delivery and DB commit are not atomic. No unique alert idempotency constraint is declared in the model. | Failed messages may never retry; overlapping jobs or a crash can also send duplicates. | Pending/sent/failed delivery states, unique idempotency key, transactional outbox and bounded retry. Count delivery success, not just event creation. |
| P1 | `run_full_ingestion()` ends with `status='success'` even when child results contain errors. eBay catches HTTP failures and returns `[]`; audit CLI skips empty results and exits normally. | Empty searches, blocked requests, missing credentials and real success are conflated; monitoring can look healthier than the system is. | Typed connector outcomes, propagated partial/error status, empty-run alarms and a nonzero audit failure exit when evidence is missing. |
| P1 | Enrichment asks for photo quality/fakeness but only passes a photo count in text (`enrichment_prompt.py:92`; `enrichment.py:138`). The score assigns a large weight to fakeness. | A text-derived heuristic looks like visual authentication or a calibrated probability. | Label unsupported assessments unknown; never treat them as authenticity proof. Add image inputs only if a held-out evaluation shows value. Keep physical checks for high-value purchases. |
| P1 | `settings.py:68` defaults enrichment to `gemini-2.0-flash`. Google lists its retirement/shutdown on 2026-06-01. | Default-enabled enrichment with credentials can call a retired model. Actual deployed override is unknown. | Select a supported exact model identifier for the active provider, test it, and add a startup probe and lifecycle check. [Gemini API lifecycle](https://ai.google.dev/gemini-api/docs/deprecations), [Vertex lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions). |
| P1 | Enrichment token/monthly budget settings have no enforcement references outside settings. Synchronous LLM calls run inside an async job; parser numeric/type conversions can raise outside the call's exception block. | Spending and worker latency can grow; malformed output can stop a batch. | Shared atomic budget accounting, per-item schema validation and failure isolation, bounded async/offloaded calls, cache by input/model/prompt version. |
| P1 | CI only runs unit tests, and builds backend/UI but not ingestion. Integration setup fails; unit parser fixtures need network; format gate currently fails. | Green unit logic does not prove the main service can ingest and deliver. | Network-free unit fixtures; PostgreSQL integration tests in the existing CI service; migration + ingestion-image build + one mocked full-flow test. Run format via Ruff, not manual edits. |
| P1 before public/customer use | Caddy protects ordinary production routes with shared Basic Auth; `/webhooks/*` is public. Telegram secret verification silently turns off if unset, and callbacks are not checked against an authorized chat/user. | Safe operation depends on deployment configuration; public callbacks can otherwise forge feedback. Shared auth is not tenant isolation. | Require the webhook secret in production, validate callback ownership, and test it. Keep the dashboard private; add tenant identity only if the SaaS branch is pursued. |
| P2 | eBay `feedbackScore` is stored as `seller_rating`, then divided by five in composite scoring (`ebay.py:253`; `composite_scoring.py:313`). | A feedback count/net score is treated as a five-star quality rating. | Preserve source semantics: count, positive-feedback fraction, account age and unknown values separately. |
| P2 | `pricing.py` labels a weighted **mean** as `weighted_median`; exponential decay uses a time constant labeled as a half-life. PMN ± standard deviation is not a calibrated prediction interval. | The calculation description overstates robustness and the meaning of uncertainty. | Correct the implementation or name, validate on chronological holdout data, and report empirical interval coverage. |
| P2 | `/health` always returns OK; deploy only probes this route. Backups remain on the same server. `backend/main.py` creates schema on import while Alembic `target_metadata=None`. | An answering process can mask dead dependencies; a host loss can lose backups; schema ownership is ambiguous and autogeneration cannot work as documented. | Separate liveness/readiness, check DB/Redis/worker age, restore a backup, add off-host copies, and make migrations authoritative. |
| P2 | Large endpoint module, multiple pricing/scoring implementations, permissive bounds on some API pagination, per-row/bulk unbounded queries, and overlapping connector paths. | Maintenance and scale costs increase, but are not the immediate revenue bottleneck. | Consolidate only around the fixes above; profile actual slow paths and add bounded queries. Avoid a general rewrite. |

The scored confidence is a hand-weighted heuristic, not a calibrated probability of profit. The feedback endpoint's “precision” measures interest/purchase feedback among responders, not whether a trade eventually made money. Both labels should be corrected before they become business KPIs.

### Small, reproducible contract examples

- Discount bug, derived from code: PMN=100 and purchase=99 produces margin_pct=-1; UI threshold=+10 passes because -1 is not greater than +10. It should fail a requirement of at least 10% discount.
- Loss-recording bug: `FeedbackUpdate(profit=-10)` fails validation because `ge=0` is declared.
- Missing detail wiring: `rg -n 'fetch_and_persist_details|persist_listing_detail' --glob '*.py'` returns definitions and the internal persistence call, but no application caller.
- Unenforced enrichment caps: search the two configuration names; there are no budget checks using them in the enrichment execution path.

## 3. The previous roadmap is not a reliable completion checklist

| Existing milestone | Current assessment | Decision |
|---|---|---|
| Trust the System | Run tracking, health, PMN history, feedback and tests exist. The todo is still unchecked. Real pricing accuracy, uninterrupted operation and integration success are not proven. | Reopen the acceptance gates rather than rebuilding existing features. |
| Fast and Precise | Composite score and enrichment tables exist. Faster scheduling, authoritative alert gating and detail integration are incomplete. | Finish the pipeline contracts before adding score factors. |
| Get Smarter | Broad discovery, trends and additional model intelligence are premature while price provenance is unreliable. | Defer. |
| Scale Up | Inventory and realized P&L are fundamental to the current goal. More marketplaces and multi-tenancy are optional. | Pull the minimal ledger forward; defer marketplace expansion and SaaS infrastructure. |

The July recovery plan's missing seed script and legacy eBay connector are now outdated: `scripts/seed.py` and Browse/OAuth exist. The August document correctly identifies the LBC sold-data issue, but its production-state claims and profit projections were not revalidated here. Neither historical document establishes current earnings. Preserve them as history and use this review's measurable gates for new work.

## 4. Use manually verified exit values to start earning sooner

Start with one category you can physically inspect and a small list of exact models. Standardized cameras/lenses, game consoles or audio hardware are candidates to **validate**, not claims of measured profitable markets. Choose based on your expertise, recent exact-model sold comparables, shipping feasibility and achievable cash cycle. Defer watches and other items whose authenticity/condition you cannot confidently assess.

Use LeBonCoin/eBay active listings for acquisition discovery where their access works. Bootstrap exit references with manually checked completed transactions in eBay Product Research and your own settled sales. eBay states that Product Research includes actual accepted Best Offer prices and precise search filters; ordinary related search results are not the same evidence. [eBay Product Research](https://www.ebay.com/help/selling/selling-tools/product-research?id=4853).

A reference record should contain exact model/variant, condition, destination marketplace, currency, sold-price evidence, source URL or reference, sale date, review date, reviewer and limitations. A manual reference must be auditable and expire. Do not assume a Product Research UI capability implies automated API access or redistribution rights. The current code does not implement a sold-data API.

For each opportunity show: exact match status, total acquisition cost, conservative exit estimate, evidence date/type, intended selling platform, every material expense, expected contribution, maximum bid, uncertainty, first-seen time and last availability check. Unknown information must remain visible. The human authorizes the purchase.

## 5. €1,000/month depends on contribution per trade and cash turnover

All prices, volumes, fee assumptions, loss allowances and timing in this section are **estimated scenarios**, not measured marketplace returns. The older strategy assumes €10,000 capital and 10–20 hours/week; these remain provisional pending user confirmation.

For a French micro-enterprise selling goods, the cited standard social-contribution rate is **12.3% of turnover**, not profit. The user's actual regime, exemptions and other charges are unknown. This scenario includes that rate to avoid presenting pre-charge trading spread as disposable profit. [Service Public: micro-social regime](https://entreprendre.service-public.gouv.fr/vosdroits/F37353).

Marketplace fees must come from the actual seller status, category and destination. eBay and LeBonCoin document variable professional fee structures; the code's universal constants are not reliable quotes. [eBay professional fees](https://www.ebay.fr/help/selling/fees-credits-invoices/store-selling-fees?id=4809), [LeBonCoin professional commission](https://assistance.leboncoin.info/hc/fr/articles/6677544788370-A-quoi-correspond-la-commission-leboncoin-).

| Estimated input / derived output | Thin-margin scenario | Working scenario | Strong-sourcing scenario |
|---|---:|---:|---:|
| Acquisition price | €250 | €300 | €350 |
| Realized sale price | €400 | €500 | €650 |
| Marketplace/payment fees, assumed 10% | €40 | €50 | €65 |
| Total logistics | €20 | €20 | €25 |
| Expected repair/return/loss allowance | €20 | €15 | €20 |
| Social contributions, scenario 12.3% of sales | €49.20 | €61.50 | €79.95 |
| **Derived contribution per completed trade** | **€20.80** | **€53.50** | **€110.05** |
| Trades to exceed €1,000 after assumed €150 monthly overhead | **56** | **22** | **11** |
| Derived monthly operating profit at that volume | €1,014.80 | €1,027.00 | €1,060.55 |

The strong-sourcing scenario requires finding the modeled purchase discounts; it is not a default expectation. Thin margins require too much handling volume for a part-time operator. The desired purchase gate is contribution after all modeled costs, not a generic percentage below asking-price PMN.

For the working scenario:

- **Derived cash requirement:** assuming €310 landed acquisition cost, 22 completed trades/month and a 28-day purchase-to-payout cycle, average deployed capital is `22 × 310 × 28 / 30 = €6,365`. Keeping 20% of capital uncommitted implies approximately **€7,957 total working capital**. Longer payout holds and returns increase the requirement.
- **Estimated operator effort:** 2 hours per completed trade plus 12 hours/month of sourcing review, administration and maintenance gives **56 hours/month**, approximately **13 hours/week**. This excludes initial engineering and learning. The old “under 30 minutes/day” ambition is not supported by this model.
- **Derived sensitivity:** selling for €450 instead of €500 cuts contribution to €15.35 with the same other assumptions; 22 sales yield only €187.70 after overhead. An exit-price error can erase most of the month.
- **Derived cash-cycle sensitivity:** a 42-day cycle raises the same capital requirement to approximately €11,935 including the 20% reserve. Faster scraping cannot solve slow inventory turnover.

Maximum purchase price should be calculated backwards:

`conservative sale proceeds − destination fees − logistics − applicable turnover charges − risk allowance − required contribution`

Store modeled reserves separately from actual realized losses and reconcile them monthly so they are not double-counted. A purchased but unsold item is inventory, not profit. Cash returned from selling it includes recovery of acquisition capital, not all new income.

### Capital scenarios, keeping the same unit economics

**Derived ceilings, not forecasts:** with 80% deployed capital, €310 landed acquisition, a 28-day cycle, €53.50 contribution and €150 overhead, €3,000 capital supports about 8 completed trades/month and €278 profit; €5,000 supports about 13 trades and €545; €10,000 supports about 27 trades and €1,294 before operator/deal-supply limits. The working scenario caps actual throughput at 22 trades. These figures floor trade counts; they assume enough profitable opportunities exist and all inventory turns on schedule.

## 6. Roadmap: proof first, then a repeatable operating month

Engineering estimates assume an experienced developer familiar with this repository. They include focused tests and integration work, exclude marketplace approval waits and operational buying/selling, and are not commitments. Total initial engineering envelope: **estimated 70–120 hours**, plus the recurring operator work above. Live access problems can materially extend it.

| Phase | Estimated engineering | Deliverables | Evidence required to proceed |
|---|---:|---|---|
| A. Stop misleading decisions | 12–20 h | Disable fake sold ingestion; inspect/quarantine affected evidence; fix discount/liquidity conventions; allow losses; block stale/missing-evidence alerts; inspect deployed health and configuration. | Deterministic regressions prove asking data never becomes sold evidence, weak discounts fail, losses save, and missing evidence cannot generate a buy recommendation. |
| B. Establish useful valuation | 18–30 h | Exact-model/variant identity; separate acquisition/reference filters; manual reference input; destination-aware net contribution and max-bid calculation; evidence expiry. | Proposed pilot: manually review 50 recent candidates across the initial models, label errors, and verify every intended purchase against independent sale evidence. No actionable candidate has an unresolved identity/provenance error. |
| C. Complete reliable delivery | 24–40 h | Wire detail collection; connect computation/scoring/eligibility; score invalidation; incremental scheduling; delivery outbox; typed errors; network-free unit tests and PostgreSQL integration CI. Keep optional LLM enrichment off until its inputs/evaluation are sound. | Proposed gate: 7 consecutive days without an unreported ingestion/delivery failure; a seeded full-flow test exercises fetch through feedback; all gate tests pass without skips. |
| D. Close the money loop | 16–30 h | Inventory ledger and trade states; fee/logistics/repair entries; purchase/sale/payout dates; signed P&L; stale-inventory list; capital committed; simple monthly export. | Proposed gate: 5 fully documented settled trades, including all costs and any losses. All unsold stock remains visible. |
| E. Operate and optimize | Ongoing; cap engineering to problems revealed by operations | Review candidates, purchase conservatively, inspect, list promptly, ship, record outcomes, reconcile cash weekly. Expand only proven models. | Proposed gate: working unit contribution and cash-cycle assumptions are observed before increasing exposure; then achieve the monthly profit objective in consecutive months. |

Calendar estimate at the provisional capacity: **4–8 weeks to a usable pilot**, delivering the purchase-critical subset first and overlapping selective engineering and operations; **3–6 months to a first €1,000 operating-profit month**; **4–8 months to demonstrate repeatability across consecutive months**. These are planning ranges with low confidence until the first trade cohort closes. The full engineering envelope alone takes 7–12 weeks at 10 engineering hours/week, before allowing for operations. At a smaller weekly time budget, extend the schedule rather than removing evidence gates.

### The first operating week

1. Implement Phase A and run the focused regressions; inventory current deployment without changing it until its state is understood.
2. Select a few exact models you can inspect. Verify exit prices manually and write a maximum-buy price for each condition/variant.
3. Review candidate listings in shadow mode. Record false matches, stale availability, missing details and bad economics; do not tune against future evaluation data.
4. Begin a small trade cohort only when references and checks support the decision. Keep exposure within an explicit loss budget; do not deploy the whole capital allocation immediately.
5. Record every cost and timestamp from the first purchase. Reconcile realized sale proceeds and unsold inventory weekly.

### Features to build before the profit target

- Auditable reference prices, variant matching, destination-aware costs and maximum bid.
- Reliable priority monitoring with honest timestamps and failure reporting.
- A single buy-candidate gate with fresh availability checks and repeat-safe delivery.
- Inventory, signed P&L, purchase-to-payout cycle and capital-exposure tracking.
- A compact operator view: “review today,” “list/ship today,” “aging stock,” and “cash/results.” Adapt the existing dashboard.

### Features to defer

Automatic product discovery, more marketplaces, elaborate dashboards, native mobile apps, autonomous purchases, seller profiling, watch authentication by LLM, and a general architecture rewrite. Also defer user accounts, billing and multi-tenancy while the goal is personal resale. Add an LLM only for a measured error class that rules and manual checks cannot handle economically.

## 7. Measure the system without leakage or proxy substitution

The current PMN-accuracy endpoint attempts forward windows, which is better than comparing every estimate with its own full input sample. However, mutable observation times/prices, relabeled sales and missing immutable reference provenance prevent a defensible out-of-sample claim. Current accuracy is **not measured** for actual resale outcomes.

Proposed evaluation protocol:

1. Freeze the exact candidate, reference set, fees, model/prompt version and score when the decision is made. Exclude the candidate itself and duplicate/relisted copies from comparables.
2. Train/tune rules only on an earlier development period. Reserve a later chronological cohort untouched for final evaluation; group the same listing/physical item so duplicates do not cross boundaries.
3. Compare a simple baseline—exact-model verified comparable median plus explicit costs—with each proposed rule/LLM change on identical inputs. Change one variable at a time, use deterministic extraction settings, and log versions/seeds.
4. Report exact-model matching precision/recall by marketplace and variant; sold-price error by condition/category; valid opportunity precision; availability at review; realized contribution; purchase-to-payout time. Separate unanswered alerts and unpurchased opportunities from purchased-outcome results to expose selection bias.
5. On small cohorts report raw counts and bootstrap intervals; do not claim that a small uplift proves improvement. Human interest is a usability signal, not profitable-trade precision. An LLM judge is not ground truth.

| Metric | Definition and proposed decision |
|---|---|
| Monthly realized operating profit | Settled-trade contribution less period operating costs, with reserves reconciled and personal income tax shown separately. This answers the financial objective. |
| Contribution per completed trade | Include winners, losses and returns. If below the working-scenario assumption, revise the volume/capital forecast immediately. |
| Capital cycle | Purchase payment to resale payout, not listing-to-sale alone. Report median and tail plus unsold stock age. |
| Quality | Manual labels of exact identity, real availability and evidence adequacy. Report counts/denominators per source; do not substitute confidence scores. |
| Latency | Record marketplace-posted→delivered only when marketplace posting time is available; otherwise separately report first-seen→delivered and the unknown pre-observation delay. |
| Reliability | Scheduled jobs due/completed/error/no-data, failed messages retried, stale references, unresolved queue backlog. |
| Efficiency | Operator minutes per trade, monthly hosting/proxy/LLM costs, tokens in/out, API latency, cost per evaluated candidate. |

Proposed monitoring target for a small priority set: a 5–15 minute scan interval and low post-detection delay, subject to allowed access and demonstrated request budgets. This is not a promise to capture every opportunity or a measured current latency. Profile wall-clock stages before increasing concurrency.

## 8. Subscriptions are an optional profit contribution, not the starting requirement

A later paid offer could be a narrow sourcing service for an audience whose needs do not directly compete with your own inventory acquisitions. Selling the same scarce deal to everyone reduces the value and creates support problems. Partition categories/geography or sell research/workflow value; disclose any shared/nonexclusive alerts.

Generic alerting already has low-price competition: **measured advertised prices at review time: InstantAlert lists €3.99/month and €7.99/month plans**. Its performance and safety claims were not independently tested. A higher price therefore needs demonstrated net-value filtering or specialized service. [InstantAlert pricing](https://instantalert.me/en).

Only test the subscription branch after a documented profitable resale cohort. Proposed validation: interview potential users, demonstrate actual anonymized outcomes with permission, offer a manually operated paid pilot, and require renewals. Do not build a full customer dashboard just because people express interest.

**Illustrative mixed-profit scenario, derived from assumptions:** 15 working-scenario flips × €53.50 = €802.50; 10 subscribers × €49 = €490 gross subscription revenue. Assume subscription contribution is 60% after its incremental fees, applicable charges, delivery and support costs: €294. Subtract shared overhead once, €150, yielding **€946.50/month**. At 17 flips the same model yields **€1,053.50**. The 60% contribution is an estimate, not a tax rate or an observed SaaS margin. Subscription founder labor must be tracked separately.

Building a secure self-service SaaS would additionally require user identity, tenant-scoped rules and feedback, private notification routing, payment entitlements, cancellation/refund handling, quotas, ownership-aware APIs/caches and support. **Estimated additional engineering: 80–160 hours**, plus acquisition/support time and unknown demand. A concierge pilot can test demand before that investment. Subscriber turnover and repeat renewal must be measured before assigning any reliable recurring-income forecast.

## 9. Decision rules and remaining unknowns

- If verified exit prices are unavailable, operate with manual comparables or pause the affected category. An arbitrary haircut to asking prices is not measured resale value.
- If contribution is too small after all charges, change sourcing/category/price discipline. More alerts or a larger model will not repair negative unit economics.
- If stock does not turn, stop expanding purchases and address listing quality, pricing and channel fit. Measure the cash-cycle tail and reserves before raising ticket size.
- If a profitable repeatable cohort exists but operating hours are the ceiling, automate the proven repetitive task. If sourcing value is shareable and buyers renew, then explore subscriptions.

Unknowns that most affect this plan: currently available capital; actual weekly time; product inspection expertise; seller account access/status and payout constraints; current production data/health; actual fee/tax regime; supply of profitable exact-model deals; realized losses/returns; and customer demand for any paid service.

The next concrete work unit is Phase A plus a minimal reference-price and trade-recording workflow. Success is a documented profitable trade, followed by a repeatable monthly cohort—not another completed feature checklist.

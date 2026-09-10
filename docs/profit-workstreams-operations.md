# Operate the personal resale workflow

The application now separates market observations, reviewed resale estimates, and actual trade outcomes. Its purpose is to support manual purchase decisions and measure monthly operating profit. It does not buy items automatically or establish that the profit target is achievable.

## Enable the workflow

1. Back up the existing database and apply `uv run alembic upgrade head` in the configured application environment before starting the updated services. The backend no longer creates tables during import. The new migrations follow the existing revision chain.
2. Review old LeBonCoin sold proxies using `uv run python scripts/quarantine_sold_proxies.py`. This is a dry run. To quarantine the reported records, use `--apply --archive /absolute/path/to/new-archive.json`; the archive must not already exist. In Docker, the command is available as `docker compose exec backend python scripts/quarantine_sold_proxies.py`; use a mounted backup directory for any apply archive. The command archives affected observations and derived records before writing. It marks unsupported sold observations stale/unknown and invalidates derived estimates. It does not delete observations. The migration already marks historical sold provenance unknown; no old record becomes verified automatically.
3. Set `WORKING_CAPITAL_EUR` to the total budget actually allocated to resale. An unset value blocks alerts. Open inventory costs reduce the available amount. This is a manually maintained budget ceiling, not a bank balance: adjust it for realized losses, withdrawals, overhead, or extra contributions. Alerts do not reserve capital; record purchases promptly and recheck cash before buying.
4. In **Products**, activate a small set of exact product variants, select allowed providers, and set a scan interval. Default: **Configured: 60 minutes**; allowed minimum: **Configured: 5 minutes**. Provider cron checks run on staggered slots, so the interval is approximate and queue time adds delay.
5. In **Valuation**, enter an independently reviewed sold comparable, exact model tokens and exclusions, normalized condition, review date/reviewer, expiry, resale destination, and every fee, charge, logistics and risk input. A zero is an explicit operator assumption. Archive superseded references: eligible active references compete on net contribution, so an older active optimistic estimate can still be selected. A reference is a reviewed estimate, not a guarantee of resale price or time to sell.
6. If a listing is missing condition or shipping, use the listing review form in **Valuation**. Enter the exact reviewed identity, condition, shipping, reviewer and expiry. The raw marketplace fields are preserved. The review stops applying after expiry or a change in captured price/title/condition/shipping; it does not make an old listing fresh.
7. Configure a Telegram rule using a discount below the reviewed exit estimate and a minimum **net contribution**, then use **Test Rule**. Provide Telegram credentials and configure `TELEGRAM_WEBHOOK_SECRET` and the expected chat for feedback. The webhook refuses unconfigured authentication in production.
8. Start the services and inspect `/health/ready`: it requires PostgreSQL, Redis and a recent worker heartbeat. Watch connector outcomes and alert delivery status. The test suite mocks external marketplaces and Telegram; a separate live connector check remains necessary in your environment.
9. For each real purchase, create an **Inventory & Profit** entry. Update actual costs, sale and settlement information; record monthly overhead once. Use the ledger, not alert-feedback profit or asking-price spreads, to assess the target. `settled` means the payout and return window are final; `returned` means returned to the supplier and the position is closed. Customer-returned stock that will be resold stays open. Corrections to closed trade costs restate the original closing month; this is a management ledger, not an immutable accounting journal.

## Understand what is measured

- **Observed:** marketplace asking price, last-seen time, condition and shipping when supplied. First observation time is preserved; meaningful changes append an observation event. Repeated unchanged prices do not create a new price event. Product price-history charts aggregate recorded change events from this release onward; they do not reconstruct unrecorded historical prices or represent daily full-market snapshots.
- **Estimated:** reviewed exit value, net contribution, maximum purchase price, and forecast inventory profit. These depend on operator assumptions. Shipping, currency, evidence, expiry, identity and condition must pass the valuation gate. Unknown inputs block an actionable valuation.
- **Measured from operator records:** proceeds received, actual trade costs, closed trade profit, monthly overhead and operating profit. Negative outcomes remain negative. Sold but unsettled items do not contribute realized profit. The inventory date is reported separately from the selected P&L month. Actual dates cannot be in the future.
- **Unknown until observed:** actual sell-through rate, repeatable monthly profit, sourcing yield, time per trade, and predictive accuracy. Heuristic confidence is explicitly uncalibrated. The application no longer presents the interval between market sales as an individual item's time to sell.

The current workflow uses EUR. The original target was $1,000/month mixed profit, primarily personal resale; choose a reporting-currency target explicitly before judging attainment. There is no implicit USD/EUR conversion or subscription revenue ledger in this release.

## What an alert means

The listing has passed the current reviewed valuation, freshness, rule and available-capital checks. The Telegram message asks for manual review. It is not confirmation of availability, authenticity, a completed sale, or profitability.

Ingestion runs under a product-level PostgreSQL lock, fetches detail candidates, refreshes statistics/scores, and queues eligible alerts. Manual provider triggers use this same pipeline. Asking-price PMN cannot authorize an alert. Unsupported eBay and LeBonCoin sold feeds return `unsupported` rather than fabricated sales.

An alert is persisted before delivery. Failed sends retry with bounded backoff, using the same event; eligibility is rechecked before every send. Database uniqueness prevents ordinary duplicate enqueueing and row locks coordinate delivery workers. Delivery is **at least once** across a process/database failure immediately after Telegram accepts a message: Telegram provides no transaction shared with PostgreSQL, so that narrow failure window can duplicate a message. Suppressed and exhausted events are retained for audit. After correcting their cause, use **Retry if eligible** in Alerts: it checks current valuation, rule and capital, preserves retry history and queues the same event. Sent and historical events cannot be manually resent.

## Scope and operational limits

- Private operator deployment behind the existing Caddy authentication. No subscription billing, multi-tenant isolation, or public SaaS release is included.
- Manual references are the first valuation source. No new licensed sold-data feed, automatic authenticity proof, or marketplace purchasing integration is included.
- References and costs need maintenance. Source access, credentials and marketplace behavior must be checked live; automated tests do not measure availability or coverage.
- Unknown shipping blocks valuation until an explicit listing review supplies it, including sources that do not provide it. Do not enter guessed zero shipping to force eligibility.
- LLM enrichment is off by default. Existing enrichment heuristics remain research tools; they do not authorize alerts or add assumed box/receipt premiums to reviewed values.
- Detail and score batches are bounded. Alerts are bounded to recent candidates, not a promise to scan all historical inventory immediately. Higher scan frequency increases provider/network use; confirm observed connector yield before expanding it.
- A monthly profit estimate is not measured revenue. Start with a narrow, fully recorded pilot; compare projected contribution against final realized trade profit, including losses, refunds, overhead and operator time, before increasing capital.

## Pilot acceptance decisions

These are **proposed operating gates**, not measured results:

- Review the first **20 alerts** manually. Record why each is actionable or rejected; do not tune and report accuracy on the same examples.
- Complete **5–10 small trades**, recording costs and time. Keep a later set of trades untouched for evaluating any subsequent scoring changes.
- Continue only if realized economics, available capital and time requirements support the monthly target. If the gap is sourcing yield, improve data coverage; if it is unit margin, narrow variants/destinations; if it is turnover, address pricing and inventory age. Do not build a SaaS layer to compensate for unproven resale economics.

## Delivery verification

**Measured, 2026-09-10–11:** 323 unit/UI tests and 24 real PostgreSQL integration tests passed; Ruff, formatting, compilation and diff checks passed. The ingestion image built successfully. Migrations passed a blank → latest → pre-change revision → latest round trip on disposable PostgreSQL. Local test runtime was Python 3.14; CI is configured for Python 3.13. External marketplace/Telegram calls were mocked, so live access and profitability remain unmeasured.

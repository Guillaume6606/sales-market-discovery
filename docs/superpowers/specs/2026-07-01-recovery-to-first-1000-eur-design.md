# Recovery Plan: From Stalled Project to First 1000 EUR Earned

**Date:** 2026-07-01
**Status:** Proposed
**Supersedes nothing** — complements `docs/BUSINESS_ROADMAP.md` (2026-04-07), which remains the operating manual once the system is live.

---

## 1. Diagnosis: Why It Never Worked End to End

The codebase is NOT half finished. 217 unit tests pass, the full pipeline
(scrape → filter → persist → PMN → enrichment → scoring → alert → feedback)
is wired, a smoke suite exists (`tests/smoke/test_01`–`test_08`), deployment
infra is written, and `.env.prod` has Telegram + Gemini configured. The
project failed for six specific, fixable reasons:

| # | Failure | Evidence |
|---|---------|----------|
| 1 | **eBay — the only source of true sold prices and the anchor of PMN — never had real data.** Both `.env` and `.env.prod` contain SANDBOX App IDs (`-SBX-` infix), so every eBay ingestion ever run returned eBay test-environment junk. Worse: the connector targets the legacy Finding API (`ingestion/connectors/ebay.py:15`), which eBay decommissioned in Feb 2025 — the production endpoint now returns an HTML error page (verified 2026-07-01). | `.env`/`.env.prod` key inspection; live curl of `svcs.ebay.com/.../FindingService/v1`; `ebay.py:15-26`. |
| 2 | **The scraped-data quality gate never passed.** The March connector audits failed and were abandoned. | `reports/connector-audit-2026-03-17/`: LeBonCoin 50% field accuracy, Vinted 16.7%. `reports/vinted-stealth-v2/`: Vinted 41.7%, verdict FAIL. Audit v4 summary is an empty table — the audit run itself produced nothing and nobody noticed. |
| 3 | **Vinted detail parser hallucinates on 404 pages** — it extracts title/price from "page not found" HTML instead of failing. | LLM notes in `reports/vinted-stealth-v2/vinted.md`: "The scraper incorrectly extracted a title, price, and currency" from 404 pages — 4 of 4 audited listings. |
| 4 | **Zero seed data.** No product templates and no alert rules exist in any migration (0001–0007 are create-only). A deployed instance idles forever: nothing to scrape, no rules to fire. This is also why the audit and smoke suite silently no-op: the audit CLI selects active products from the DB (`ingestion/audit_cli.py:314-322`) and smoke tests 02–05 skip via the `known_product` fixture (`tests/smoke/conftest.py:34`) — "tests pass" never meant "system works". | Migrations dir; audit v4 empty table is this failure mode in action. |
| 5 | **Deployment died silently.** VPS was set up (April 22 commits: Caddy, systemd, backups, `make deploy`) but the domain no longer resolves. | `smd.guillaumequinquet.fr` → NXDOMAIN. VPS `15.235.186.227` still answers SSH. Container state unknown. |
| 6 | **Work stopped at the worst point** — after building features (enrichment, scoring, explorer UI) but before ever operating the system. Last commits 2026-04-22 ("bulk update"). | Git history. `BUSINESS_ROADMAP.md` §2 already said it in April: "feature-complete MVP that has never been used in anger." |

Secondary issues (not blockers, handle in-flight):
- Two Vinted connector implementations (`vinted.py` scraper is active via
  `ingestion/ingestion.py:16`; `vinted_api.py` is dangling) — pick one,
  quarantine the other.
- `fetch_leboncoin_api_sold()` (`leboncoin_api.py:374-378`) **relabels active
  listings as sold data** (LBC has no sold API). This silently pollutes
  sold-based PMN with asking prices — it must stop masquerading as sold data.
- Daily-only ingestion (2–4 AM crons) — deals are gone by morning. M2's
  frequency work was never done.

## 2. Strategy

Two credible approaches were considered:

- **A. "Deploy now, fix what breaks"** — follow BUSINESS_ROADMAP as written
  (it assumes connectors work). Fastest to first alert, but the audits prove
  the data was garbage and the eBay pipeline is dead. Acting on wrong
  prices/conditions loses real money on the first flips and destroys trust
  in the system again — which is exactly how the project died last time.
- **B. "Trust gate first, then operate"** (RECOMMENDED) — spend roughly a
  week making the data provably correct (rebuild eBay on a live API, audit
  ≥80% on revenue-critical fields), seed the system with idempotent scripts,
  THEN deploy and run the BUSINESS_ROADMAP operating plan.
- **C. "Scrapers-only, drop eBay"** — rejected as the plan, kept as the
  fallback: without any sold-price source, PMN degrades to active-listing
  statistics and margins must be padded. Only if eBay sold data proves
  unobtainable (see Phase 1 contingency).

## 3. The Plan

### Phase 0 — Reconnect and verify foundations (1 day)

Goal: know what state production is in; confirm which data sources are alive.

- [ ] **[USER DECISION]** Authorize SSH to the VPS (`make remote-status`,
      `make remote-health`). Claude's SSH attempt was permission-blocked;
      either run it yourself or allow the action.
- [ ] Inventory VPS: containers running? DB has data worth keeping? Which
      git revision is deployed? Disk/RAM state?
- [ ] eBay reality check: create/verify a **production** keyset at
      developer.ebay.com and confirm which APIs the account can access
      (Browse API for active listings; Marketplace Insights for sold data is
      approval-gated — apply, but do not plan on it).
- [ ] **[USER DECISION]** DNS: restore `smd.guillaumequinquet.fr` (A record
      → 15.235.186.227). Default: restore it — the Telegram webhook (feedback
      buttons) needs a hostname with a valid certificate, and reusing the
      existing Caddy config is the lowest-effort path. If you prefer not to,
      the fallback is polling `getUpdates` instead of the webhook, which is a
      small code change to schedule.
- [ ] Repo housekeeping: merge `feature/listing-explorer-ui` → `master` (it
      contains all the scoring/enrichment/UI work), triage untracked
      `.claude/` and `AGENTS.md`.
- Exit gate: VPS state known; eBay production credentials in hand; DNS
  decision made.

### Phase 1 — Make the data trustworthy (4–6 days)

Goal: every field that decides whether money is made (`price`, `title`,
`condition_norm`, `url`, `listing_id`, `is_sold`) is provably correct on the
sources we keep.

**eBay rebuild (the critical path, ~2–3 days):**
- [ ] Migrate `ingestion/connectors/ebay.py` from the dead Finding API to the
      **Browse API** (`api.ebay.com/buy/browse/v1/item_summary/search`,
      OAuth2 client-credentials) for active listings. Keep the `Listing`
      contract; the 26 existing parser unit tests define expected semantics.
- [ ] Sold data, in order of preference: (a) Marketplace Insights API if
      granted; (b) scrape eBay completed/sold search pages
      (`LH_Sold=1&LH_Complete=1`) using the existing stealth stack; (c) fall
      back to strategy C — PMN from active-listing statistics with margin
      thresholds raised ~10 points to compensate. Decide by end of Phase 1
      based on what works, and record the choice in the audit report.

**Scraper fixes (~1–2 days):**
- [ ] Fix Vinted 404 hallucination: detail parser must detect error pages and
      return a failure, never fabricated fields. Unit test with a real 404
      HTML fixture.
- [ ] Resolve Vinted connector duality: keep whichever survives a live 10-run
      reliability test, quarantine the other.
- [ ] Fix LBC condition mapping (0% accuracy in the March audit).
- [ ] Stop `fetch_leboncoin_api_sold()` from relabeling active listings as
      sold; either return nothing (and let PMN weight eBay) or store them
      explicitly flagged as asking-price proxies.

**Prove it (~1 day):**
- [ ] Write `scripts/seed_products.py` NOW (idempotent, checked in) and seed
      3–5 well-known products locally — the audit CLI and smoke tests 02–05
      are no-ops on an empty DB, which is how the March audit produced an
      empty report without anyone noticing.
- [ ] Fix the audit harness to fail loudly (non-zero exit, alert) when a
      connector yields zero listings; audit against listings fetched within
      the last hour, not stale DB rows (the March audit partly graded the
      scraper against listings that had already expired into 404s).
- [ ] Re-run `make audit` per connector. Target: ≥80% on the revenue-critical
      fields. Order: eBay, LBC, Vinted.
- [ ] Run smoke tests 01–05 against the locally seeded stack with zero skips.
- Exit gate: audit green (≥80%) for **eBay and LBC**; smoke 01–05 pass
  un-skipped. Vinted may lag without blocking the phase — but then Vinted
  listings are excluded from alerting until it passes.

### Phase 2 — Seed and deploy (1–2 days + 3–5 day soak)

Goal: a deployed system that has something to do.

- [ ] Extend `scripts/seed_products.py` to 15–20 products using
      `MarketOpportunitiesDiscovery/Analyse_Produits_Achat_Revente.xlsx` plus
      BUSINESS_ROADMAP §5 criteria (liquid, 100–500 EUR, identifiable,
      condition-stable, ≥20% spread). Seeding constraint: every product MUST
      be liquid on eBay so its PMN rests on the strongest source — LBC/Vinted
      are where you buy; eBay PMN is how you price.
- [ ] Write `scripts/seed_alert_rules.py` — 2–3 conservative rules: margin
      >25%, PMN confidence >0.6, immediate Telegram only for composite
      score >80.
- [ ] Deploy: `make deploy`, `make remote-health` green, Telegram test
      message received, webhook (or polling fallback) verified end to end by
      pressing a feedback button.
- [ ] Uptime monitoring: UptimeRobot/Healthchecks.io on `/health/overview`.
- [ ] Soak 3–5 days building PMN baselines, LLM enrichment ON (budget cap
      already enforced). During the soak, spot-check PMN for 5 products
      against your own market intuition.
- Exit gate: 5/5 services green for 5 consecutive days, PMN confidence >0.6
  on ≥10 products, first alerts arriving on Telegram.

### Phase 3 — Operate: first flip (operation weeks 1–3)

Goal: first purchase, first resale, first euros. Operator work, not
engineering.

- [ ] Follow the daily 15–20 min routine in BUSINESS_ROADMAP §5 verbatim.
- [ ] Calibration rule: give feedback (interested / not interested) on the
      first ~10 alerts WITHOUT buying. If precision is <50%, tighten
      thresholds before spending a euro.
- [ ] Raise ingestion to every 4h for the top 10 products (the one M2
      engineering item worth doing now — a small ARQ scheduling change).
- [ ] First buy on a high-confidence alert (≤150 EUR exposure until 5
      profitable flips). Working capital assumption: 500–1000 EUR per
      BUSINESS_ROADMAP §10 — **[USER DECISION]** confirm.
- [ ] Track every flip using the existing feedback pipeline: Telegram
      "Purchased" button, then record profit via the already-built
      `PATCH /alerts/feedback/{feedback_id}` (`backend/routers/feedback.py:191`).
- Exit gate: first completed flip (bought, resold, money received) by end of
  operation week 3.

### Phase 4 — Ramp to 1000 EUR cumulative gross profit (operation weeks 3–14)

The arithmetic: at 30–50 EUR average profit/flip, 1000 EUR = 20–33 flips. At
the roadmap's ramp (1–2 flips/week in month 1 → 3–5/week in months 2–3), the
1000 EUR cumulative mark lands in **operation weeks 8–14** (week 8 needs the
optimistic end; the 30 EUR/flip + slow-ramp case lands ~week 14). Levers if
behind:

- More products (20 → 40, weekly additions of 2–3 per BUSINESS_ROADMAP §5).
- Fresher data (4h → 2h ingestion on top performers).
- Threshold tuning from the feedback loop (`GET /analytics/alert-precision`).
- Cross-platform spread view (Tier 2 #11 in BUSINESS_ROADMAP) — the single
  highest-value feature if deal flow is the constraint.

Explicitly NOT doing until 1000 EUR is banked: product auto-discovery, new
marketplaces, seller intelligence, dashboard v2, multi-tenant, auto-pricing.
Feature work capped at ~20% of time; the rest is operating.

## 4. Risks

| Risk | Mitigation |
|------|------------|
| eBay sold data unobtainable (Marketplace Insights denied, sold-page scraping blocked) | Contingency C is pre-planned: PMN from active-listing statistics, margin thresholds +10 pts, act only on the widest spreads. Decide by end of Phase 1, don't drift. |
| Scrapers break again (LBC/Vinted drift every 2–3 months) | Health alerts already wired; the loud-failure audit harness (Phase 1) becomes the regression detector; budget 2–4h/month maintenance. |
| Anti-bot blocking on VPS IP (datacenter IPs are easier to flag) | Playwright stealth in place; add a residential proxy (5–10 EUR/month) only when a connector's success rate drops, not preemptively. |
| PMN wrong for products without eBay sold data | Phase 2 seeding constraint (eBay-liquid products only). Act only on PMN confidence >0.6. |
| Alert fatigue → operator abandons (how the project died before) | Conservative thresholds, tiered alerting, 10-alert calibration before first buy, 15–20 min/day cap. |
| First flips lose money | ≤150 EUR exposure/flip until 5 profitable flips; 60-second manual listing check before every buy. |

## 5. Success Criteria

1. Connector audit ≥80% on revenue-critical fields for **eBay and LBC**
   (Vinted target too, but non-blocking — excluded from alerts until green).
2. 7 consecutive days of green health on the VPS (the original M1 exit
   criterion, finally met).
3. Alert precision >50% by operation week 2, >70% by week 6.
4. First completed flip by end of operation week 3.
5. **1000 EUR cumulative gross profit by operation week 14** (stretch:
   week 8). "Earned" = gross profit from flips; infra costs (~35–90
   EUR/month) tracked separately.

## 6. Decision Points for the Operator

| # | Decision | Default if no answer |
|---|----------|---------------------|
| 1 | Allow SSH access to the VPS for Phase 0 assessment | You run `make remote-status` / `make remote-health` yourself and paste the output |
| 2 | DNS: restore `smd.guillaumequinquet.fr` vs new hostname vs no public DNS | Restore the existing subdomain (webhook needs a hostname + valid cert; existing Caddy config already expects it). Polling fallback exists if you refuse public DNS. |
| 3 | eBay production keyset: create it and apply for Marketplace Insights | Required — no default. Without production eBay credentials the plan degrades to contingency C. |
| 4 | Confirm 500–1000 EUR working capital and ~30 min/day | Assumed yes (it is what BUSINESS_ROADMAP already commits to) |

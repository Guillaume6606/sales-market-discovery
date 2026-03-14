# Arbitrage Platform: Objectives, KPIs & Roadmap

**Date:** 2026-03-14
**Status:** Approved
**Author:** Guillaume + Claude

## Vision

A self-maintaining arbitrage detection system that reliably surfaces 1-2 high-confidence opportunities per day across liquid, high-value product categories. Trustworthy enough that you can act in under 60 seconds from alert.

## Context

The platform targets high-value, liquid items (electronics, watches, and similar categories) across LeBonCoin, eBay, and Vinted. Minimum viable margin: 50-100 EUR per item. The operator (Guillaume) spends <30 min/day interacting with the system. Exceptional deals arrive as real-time Telegram alerts; the full opportunity pipeline is browsed via a dashboard during daily review sessions.

Primary use: personal semi-automated arbitrage business generating 1-2K EUR/month within 6 months. Architecture should remain clean enough to support multi-tenant SaaS later, but that is not a near-term goal.

## Strategic Objectives

### 1. Trustworthy Signals

Every alert is worth evaluating. The system earns trust through accurate pricing, low false positive rates, and transparent confidence indicators.

### 2. Operational Reliability

The platform runs unattended for weeks. Failures are detected and surfaced within minutes. No silent data gaps.

### 3. Expanding Coverage

The platform progressively discovers new product opportunities beyond what was manually seeded. Coverage grows from tens to hundreds of tracked products.

## KPIs

### Trustworthy Signals

| KPI | Target | Measurement |
|-----|--------|-------------|
| Alert precision | >90% of alerts are genuine opportunities | Track acted-on vs dismissed per alert |
| PMN accuracy | Within +/-5% of actual resale price | Compare predictions against sold prices (rolling 30d) |
| LLM filter effectiveness | Reduces false positives by >50% vs rules alone | A/B comparison of rejection rates and post-filter precision |
| Deal conversion rate | >30% of opened alerts lead to purchase | Click-to-purchase funnel tracking |

### Operational Reliability

| KPI | Target | Measurement |
|-----|--------|-------------|
| Ingestion success rate | >95% of scheduled runs complete | Log success/failure per cron run, alert on consecutive failures |
| Data freshness | All products scraped within last 24h | Monitor last_ingested_at, alert on staleness |
| Scraper health | <5% of listing fetches fail | Per-connector error rate tracking |
| Alert latency | <15 min from listing appearance to Telegram | Timestamp tracking through pipeline |

### Expanding Coverage

| KPI | Target | Measurement |
|-----|--------|-------------|
| Active tracked products | 50+ at 3 months, 100+ at 6 months | Count of active product templates |
| Revenue per month | 500 EUR at 3 months, 1-2K EUR at 6 months | Manual tracking initially, dashboard later |
| New product discovery rate | 2-3 viable suggestions per month | Count of auto-suggested products that get activated |
| Category diversity | 3+ categories with active deals | Count distinct categories with conversions |

## Roadmap

### Milestone 1: Trust the System (Weeks 1-4)

**Goal:** Platform runs unattended and you trust what it tells you.

**Deliverables:**

- **Observability and health monitoring.** Ingestion success/failure tracking per connector. Staleness alerts when a product hasn't been scraped in 24h+. System failure notifications via Telegram (separate from opportunity alerts).
- **PMN validation and backtesting.** Compare PMN predictions against actual sold prices. Surface accuracy metrics on dashboard. Flag products where PMN confidence is low (sample size <15 sold items).
- **Test coverage for core paths.** Unit tests for PMN calculation, filtering pipeline, and connector parsing. Integration tests for the ingestion-to-compute-to-alert flow.
- **Feedback loop.** "Interested" / "Not interested" responses on Telegram alerts (inline keyboard). Same actions available in dashboard. Feeds precision tracking from day one.
- **Data quality.** Deduplication improvements. Handle edge cases: listings with missing prices, stale listings that sold but weren't marked.

**Exit criteria:** Platform runs 7 consecutive days without silent failures. Ingestion health visible at a glance. PMN accuracy is measured.

### Milestone 2: Fast and Precise (Weeks 5-8)

**Goal:** Alerts are high-quality and arrive fast enough to act on.

**Deliverables:**

- **Higher ingestion frequency.** Move from daily cron to every 2-4 hours for high-priority products. Configurable per product template.
- **Composite opportunity score.** Single 0-100 score combining: margin vs PMN, liquidity, listing age, seller rating, LLM assessment. Transparent breakdown available per listing.
- **Tiered alerting.** Score >80: immediate Telegram alert. Score 50-80: dashboard only. Score <50: suppressed. Thresholds configurable per alert rule.
- **LLM validation activation.** Enable Gemini for top opportunities. Validate that it reduces false positives. Tune prompts using feedback data from M1.
- **Pipeline latency optimization.** Measure end-to-end latency. Target: listing appears on marketplace to Telegram alert in <15 minutes.

**Exit criteria:** Alert precision >80% (stepping stone toward the 90% KPI target). Acting on 1+ deal per week. Pipeline latency under 30 minutes.

### Milestone 3: Get Smarter (Weeks 9-14)

**Goal:** The platform finds opportunities you wouldn't find yourself.

**Deliverables:**

- **Product discovery engine.** Analyze sold data across categories to identify products with high volume and high price spread (arbitrage potential). Surface suggestions in dashboard with supporting evidence.
- **Trend detection.** Alert when a product's PMN shifts significantly. Weekly digest of market movements (price drops = buying opportunities, price rises = sell signals).
- **Advanced LLM analysis.** Multi-image assessment of listing photos (not just page screenshots). Condition estimation. Red flag detection for fake or misleading listings.
- **Seller intelligence.** Track seller patterns. Flag professional resellers (competing). Prefer private sellers with good ratings.
- **Dashboard v2.** Opportunity cards with photos, score breakdown, one-click actions, historical deal performance.

**Exit criteria:** Platform has suggested 5+ new products not manually seeded. 50+ products actively tracked. Revenue trending upward.

### Milestone 4: Scale Up (Weeks 15-20)

**Goal:** Sustainable 1-2K EUR/month with minimal daily time.

**Deliverables:**

- **Portfolio management.** Track inventory: purchased items, resale listings, profit/loss per item. Close the loop from opportunity to realized profit.
- **Additional marketplaces.** Facebook Marketplace, Rakuten, Amazon Warehouse.
- **Auto-pricing for resale.** Suggest optimal listing price per platform based on current market data when you buy an item to flip.
- **Performance analytics.** Monthly P&L, best/worst categories, ROI by product type, time-to-sell metrics.
- **Multi-tenant foundations (optional).** User accounts, isolated product sets, separate Telegram channels. Only if SaaS path is pursued.

**Exit criteria:** 1-2K EUR/month sustained over 2+ consecutive months. <30 min/day operator time. Clear visibility into what's working.

### Timeline Summary

```
Weeks  1-4:   M1 "Trust the System"   — reliability, testing, feedback loop
Weeks  5-8:   M2 "Fast and Precise"   — speed, scoring, alert quality
Weeks  9-14:  M3 "Get Smarter"        — discovery, trends, advanced LLM
Weeks 15-20:  M4 "Scale Up"           — portfolio, new marketplaces, analytics
```

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Scrapers break frequently | No data, no deals | Per-connector health alerts. Fallback strategies (API where available, multiple parsing paths). Already partially implemented for LeBonCoin. |
| PMN inaccurate for low-volume products | Bad signals, lost trust | Confidence flag when sample <15 sold items. Suppress alerts for low-confidence PMN until validated. |
| LLM costs escalate | Budget pressure | LLM runs only after rule-based filters pass. Cache results per listing. Daily budget cap. |
| Marketplace ToS / rate limiting | Blocked or banned | Respectful scraping intervals (already implemented). Rotate IPs if needed. Prefer official APIs. Monitor 403/429 responses. |
| Operator loses motivation before M2 | Project stalls | M1 delivers visible value fast. Feedback loop and health dashboard make the platform feel alive before revenue flows. |
| Product discovery suggests junk | Wasted time | Human approval gate. Suggestions are proposals, never auto-activated. Show supporting evidence. |

## Assumptions

- Telegram bot is already functional (token and chat ID configured).
- Google Gemini API will be available and affordable by M2. Expected LLM costs under 20 EUR/month at planned volume (LLM runs only on post-filter listings).
- Operator has working capital for purchasing arbitrage items.
- KPI measurement for alert precision and deal conversion begins mid-M1, once the feedback loop ships. Earlier milestones rely on qualitative assessment.

## Current State (as of 2026-03-14)

The platform is surprisingly mature for a v0.1:

- **Production-ready:** All 3 marketplace connectors, PMN engine, liquidity scoring, ARQ worker (15 tasks, 5 cron jobs), alert engine, Telegram integration, multi-stage filtering, anti-bot scraping
- **Implemented but disabled:** LLM validation (needs API key), screenshot capture (opt-in)
- **Beta:** Streamlit dashboard (basic structure, needs polish)
- **Missing:** Test coverage, observability/health monitoring, feedback loop, opportunity scoring, product discovery

The roadmap builds on this foundation rather than replacing it.

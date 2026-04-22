# Business Roadmap: Sales Market Discovery

**Date:** 2026-04-07
**Author:** Guillaume (Product Owner)
**Status:** Active
**Revision:** 1.0

---

## 1. Product Vision & Value Proposition

### The Problem

Finding underpriced items on resale marketplaces (eBay, LeBonCoin, Vinted) is a full-time manual grind. You scroll for hours, miss deals because someone else was faster, overpay because you don't know the real market price, and get burned by fakes or misleading listings. There is no way to systematically monitor thousands of listings across three platforms at once.

### The Solution

An automated arbitrage radar that:
- Monitors eBay, LeBonCoin, and Vinted 24/7
- Knows the real market price (PMN) for every tracked product
- Scores every listing on margin, risk, and confidence
- Sends a Telegram alert when a high-confidence deal appears
- Lets the operator act in under 60 seconds

### Who Is This For

**Solo reseller (you).** Someone with working capital (1-5K EUR), 30 minutes/day of attention, and the ability to buy and reship items across French/European marketplaces. The system does the hunting; you do the buying and selling.

### The Money Equation

Buy an item for 30% below its market normal. Sell it at market price on a different platform. After fees (8-16%) and shipping (5-8 EUR), net 50-150 EUR per flip. Do this 3-5 times per week. That is 600-3000 EUR/month gross margin.

---

## 2. Current State Assessment

### What Works Today (as of April 2026)

| Component | Status | Notes |
|-----------|--------|-------|
| 3 marketplace connectors (eBay API, LBC scraping, Vinted scraping) | Working | Scheduled daily ingestion at 2-4 AM |
| PMN (market price normal) computation | Working | Hybrid algorithm, confidence scoring, backtesting history |
| Composite opportunity scoring | Working | Spread, ROI, risk-adjusted confidence (0-100) |
| LLM enrichment (Gemini Flash) | Working | Urgency, condition, fakeness detection. Hourly batch |
| Detail fetching (descriptions, photos, seller info) | Working | Rate-limited, candidate-selected |
| Alert system with Telegram | Working | Rule-based triggers, inline feedback buttons |
| 7-page Streamlit dashboard | Working | Home, Discovery, Listing Explorer, Products, Import, Health, Alerts |
| Observability (health, staleness, ingestion tracking) | Working | Per-connector metrics, PMN accuracy, enrichment coverage |
| Unit + smoke tests | Working | pytest, CI-ready |
| Docker Compose deployment | Working | 5 services: db, redis, backend, ingestion, ui |

### What Is NOT Working / Missing

| Gap | Impact on Revenue | Effort to Fix |
|-----|-------------------|---------------|
| **Not deployed to production** -- runs only on local machine | No 24/7 monitoring = missed deals | M (1-2 days) |
| **Daily ingestion only** -- deals go stale in hours | Competitors grab deals before you | S (half day) |
| **No products configured** -- empty product templates | Zero opportunities surfaced | S (1 day of research) |
| **Alert rules not tuned** -- thresholds may be wrong | Too many junk alerts or too few good ones | S (iterative) |
| **No inventory tracking** -- can't close the profit loop | No idea if the business is actually profitable | L (1 week) |
| **Milestone 1 tasks incomplete** -- feedback loop, data quality items still unchecked | Feedback not feeding back into precision | M (3-4 days) |
| **Connector audit dashboard** -- partial | Can't detect scraper regressions visually | S |
| **No higher-frequency ingestion** -- M2 deliverable not done | Slow pipeline = stale alerts | M |

### The Gap to Revenue

The platform is a **feature-complete MVP** but has **never been used in anger**. The gap to first euro is not technical -- it is operational:

1. Deploy it somewhere it runs 24/7
2. Seed it with 10-20 well-researched product templates
3. Let it collect data for 3-5 days to build PMN baselines
4. Configure alert rules with sensible thresholds
5. Start acting on alerts

---

## 3. Revenue Model

### Unit Economics (Per Flip)

```
Buy price:          200 EUR (listing at 30% below PMN of ~285 EUR)
Shipping to you:      7 EUR
Platform buy fees:    0-10 EUR (varies by platform)
---
Acquisition cost:   ~217 EUR

Sell at PMN:        285 EUR
Platform sell fees:  -23 to -45 EUR (8-16% depending on platform)
Shipping to buyer:   -7 EUR
---
Net proceeds:       233-255 EUR

Profit per flip:    16-38 EUR (conservative, 30% spread)
Profit per flip:    50-100 EUR (good deals, 40-50% spread)
```

### Monthly Revenue Targets

| Phase | Deals/Week | Avg Profit/Deal | Monthly Revenue | Timeline |
|-------|-----------|-----------------|-----------------|----------|
| Learning (Month 1) | 1-2 | 30-50 EUR | 120-400 EUR | Weeks 1-4 |
| Ramping (Month 2-3) | 3-5 | 50-80 EUR | 600-1600 EUR | Weeks 5-12 |
| Cruising (Month 4+) | 5-8 | 60-100 EUR | 1200-3200 EUR | Weeks 13+ |

### Cost Structure

| Item | Monthly Cost | Notes |
|------|-------------|-------|
| VPS hosting (Hetzner/OVH) | 15-30 EUR | 4 vCPU, 8GB RAM, 80GB SSD |
| Gemini API (LLM enrichment) | 20-60 EUR | Budget-capped at 120 EUR/month max |
| eBay API | Free | Finding API has generous free tier (5000 calls/day) |
| Telegram bot | Free | |
| Domain + SSL | ~1 EUR/month | Optional, for dashboard access |
| **Total infra** | **~35-90 EUR/month** | |

### Break-even

At 35-90 EUR/month cost, break-even is **1-2 successful flips per month**. That is extremely achievable even while learning.

---

## 4. Deployment Roadmap

### Phase 1: Get It Running (Days 1-2)

**Target: VPS with Docker Compose, accessible 24/7.**

1. **Provision a VPS**
   - Hetzner CX31 (4 vCPU, 8GB RAM, 80GB SSD) -- ~15 EUR/month
   - Ubuntu 24.04, Docker + Docker Compose installed
   - Alternatives: OVH VPS, Scaleway DEV1-L

2. **Clone repo, configure `.env`**
   - Set real `EBAY_APP_ID` (register at developer.ebay.com if not done)
   - Set `GEMINI_API_KEY` (Google AI Studio, free tier to start)
   - Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
   - Set strong Postgres password

3. **`make up` on the VPS**
   - Verify all 5 services healthy: `make health`
   - Run `make migrate` to apply DB schema

4. **Expose dashboard (optional)**
   - Nginx reverse proxy with basic auth or Tailscale for private access
   - Do NOT expose the backend API publicly without auth

5. **Set up a simple backup**
   - Daily pg_dump cron to a separate disk or S3-compatible storage
   - Cost: ~2 EUR/month for object storage

### Phase 2: Monitor & Harden (Days 3-7)

1. **Uptime monitoring**
   - Free: UptimeRobot or Healthchecks.io pinging `/health` every 5 min
   - Configure Telegram alert on downtime

2. **Log rotation**
   - Docker log rotation config (max 50MB per container, 3 files)
   - Or ship to free Grafana Cloud (50GB/month free)

3. **Auto-restart**
   - Docker Compose `restart: unless-stopped` (already configured)
   - Systemd service for Docker Compose to survive VPS reboots

4. **Security basics**
   - SSH key only (disable password auth)
   - UFW firewall: allow 22, 80/443 only
   - Keep `.env` out of any public repo

### Phase 3: Scale When Needed (Month 2+)

- If database grows past 10GB: add dedicated Postgres (Supabase free tier or managed PG)
- If ingestion needs more frequency: bump VPS to 8GB RAM
- If multi-user needed later: Kubernetes on Hetzner Cloud (~40 EUR/month)

---

## 5. Configuration & Operations

### Day 0: Product Research (Most Important Step)

The system is only as good as the products you track. Spend a full day on this.

**Criteria for a good product template:**
- Liquid: 10+ items sold per month on at least 2 platforms
- High-value: 100-500 EUR price range (sweet spot for margins)
- Identifiable: clear model name, searchable, not generic
- Condition-stable: electronics, watches, audio -- not fashion (too subjective)
- Price spread: at least 20% gap between lowest active and median sold

**Starter categories to seed:**
- Consumer electronics: AirPods Pro, PlayStation 5, Nintendo Switch, specific GPU models
- Watches: Seiko Presage, Tissot PRX, Casio G-Shock specific refs
- Audio: Sonos speakers, Bose QC headphones, specific DAC models
- Photography: Canon/Sony lens models, GoPro specific versions
- Gaming: Steam Deck, specific controller models

**For each product, create a template with:**
- Precise `search_query` (model number, not generic terms)
- `brand` (for filtering)
- `price_min` / `price_max` (exclude junk and overpriced)
- `words_to_avoid` (e.g., "case", "strap only", "broken", "for parts")
- Enable all 3 providers unless the product doesn't exist on one
- Start with `enable_llm_validation = false` (turn on after PMN baseline is built)

**Seed 15-20 products across 3-4 categories.** This gives enough data diversity for the system to prove itself.

### Daily Operations (15-20 min/day)

| Time | Action | Where |
|------|--------|-------|
| Morning | Check Telegram for overnight alerts | Phone |
| Morning | Act on high-confidence alerts (click listing, evaluate, buy or dismiss) | Phone + marketplace apps |
| Evening | 5 min: Dashboard health check (any red connectors? stale products?) | Streamlit UI |
| Evening | 5 min: Review Listing Explorer for deals the alert system may have missed | Streamlit UI |
| Evening | 5 min: Check alert precision metrics -- are you dismissing too many? Adjust thresholds | Streamlit UI |

### Weekly Operations (30-60 min/week)

| Action | Purpose |
|--------|---------|
| Review PMN accuracy page | Are your tracked products' market prices reliable? |
| Add 2-3 new product templates | Expand coverage |
| Deactivate underperforming products | Products with no sales or bad PMN confidence |
| Check ingestion run history | Any connector failures you missed? |
| Update `words_to_avoid` on noisy products | Reduce false positive listings |
| Record profit on completed flips (alert feedback) | Feed the precision loop |

### Monthly Operations (1-2 hours)

| Action | Purpose |
|--------|---------|
| P&L review: total spent vs total sold | Are you actually making money? |
| Category performance review | Which categories have best ROI? Double down |
| Alert rule tuning | Adjust thresholds based on 30-day precision data |
| Check LLM enrichment costs | Stay within budget cap |
| System update: `git pull && make up` | Get latest features and fixes |

---

## 6. Feature Prioritization

### Tier 1: Must-Have for First Euro (Weeks 1-3)

These items are blockers to generating any revenue.

| # | Feature | Why | Effort | Revenue Impact |
|---|---------|-----|--------|----------------|
| 1 | **Deploy to VPS** | Can't make money if it only runs on your laptop | S | Critical |
| 2 | **Seed 15-20 product templates** | Zero products = zero alerts | S | Critical |
| 3 | **Increase ingestion to every 4h for priority products** | Daily is too slow -- deals are gone in hours | M | High |
| 4 | **Tune alert thresholds based on first week of data** | Too many false alerts = alert fatigue | S | High |
| 5 | **Complete Milestone 1 feedback items** (Telegram webhook feedback, precision tracking wired end-to-end) | Need the learning loop to improve over time | M | High |
| 6 | **Enable LLM enrichment in production** | Fakeness detection and condition confidence reduce bad buys | S | Medium |

### Tier 2: Growth Accelerators (Weeks 4-8)

These increase deal volume and quality after the basics work.

| # | Feature | Why | Effort | Revenue Impact |
|---|---------|-----|--------|----------------|
| 7 | **Inventory / flip tracker** -- simple table: bought item, buy price, sold price, profit | Close the feedback loop on actual revenue | M | High |
| 8 | **Product auto-discovery** -- analyze sold data to suggest new products | Grow from 20 to 100+ tracked products without manual research | L | High |
| 9 | **Ingestion every 1-2h for top products** | Catch deals faster | S | Medium |
| 10 | **Price trend alerts** -- weekly digest of PMN shifts | Know when to buy (dip) or sell (peak) | M | Medium |
| 11 | **Cross-platform arbitrage detection** -- same item cheaper on platform A vs B | The core arbitrage use case, made explicit | M | High |
| 12 | **Condition-aware pricing** -- adjust PMN by condition tier | More accurate margins, fewer surprises | S | Medium |

### Tier 3: Nice-to-Have (Month 3+)

| # | Feature | Why | Effort | Revenue Impact |
|---|---------|-----|--------|----------------|
| 13 | **Seller intelligence** -- track pro sellers, prefer private sellers | Avoid competing with other resellers | L | Low-Medium |
| 14 | **Auto-pricing tool** -- suggest optimal sell price when you list an item | Sell faster and at better margins | M | Medium |
| 15 | **Email digest** (daily/weekly summary in addition to Telegram) | Backup channel, better for weekly review | S | Low |
| 16 | **Mobile-friendly dashboard** (or lightweight PWA) | Act faster on the go | L | Low |
| 17 | **New marketplaces** (Facebook Marketplace, Rakuten) | More coverage = more deals | XL per connector | Medium |
| 18 | **Multi-user / SaaS** | Monetize the platform itself | XL | Future |

---

## 7. Risk Assessment

### High Risk

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Scrapers break** (LBC/Vinted change their HTML/APIs) | High (every 2-3 months) | Ingestion stops for that platform | Connector health alerts trigger immediately. Keep eBay API as stable fallback. Budget 2-4h/month for scraper maintenance |
| **Anti-bot blocking** (IP banned, CAPTCHA walls) | Medium-High | Connector returns zero data | Playwright stealth already in place. Add residential proxy rotation (5-10 EUR/month) if needed. Respect rate limits |
| **PMN inaccurate for niche products** | High for low-volume items | Bad buy decisions, losses | Only act on high-confidence PMN (>0.6). Start with liquid products. Check PMN accuracy dashboard weekly |
| **Alert fatigue** (too many low-quality alerts) | Medium | Stop checking alerts, miss real deals | Start conservative (high thresholds). Use tiered alerting: only score >80 hits Telegram. Review precision weekly |

### Medium Risk

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **LLM costs exceed budget** | Low (budget cap exists) | Enrichment stops mid-month | Budget cap at 120 EUR/month already configured. Monitor via health dashboard. Fall back to rule-based scoring |
| **Legal / ToS issues** | Low-Medium | Account banned on marketplace | Never auto-buy. Scraping is read-only. Use official APIs where available. Don't hammer endpoints |
| **Working capital tied up** | Medium | Can't buy new deals while items are listed | Start with fast-moving categories (electronics sell in 3-7 days). Keep a 1K EUR reserve |
| **Operator burnout** | Medium | Stop using the system | Keep daily ops to 15-20 min. Automate everything possible. Focus on high-ROI actions only |

### Low Risk

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **VPS failure** | Low | 1-2h downtime | Docker auto-restart. Daily DB backup. Can redeploy in 30 min |
| **Data loss** | Low | Lose PMN history | Daily pg_dump. Object storage backup |

---

## 8. KPIs & Success Metrics

### Weekly Dashboard (check every Sunday)

| KPI | Target (Month 1) | Target (Month 3) | How to Measure |
|-----|-------------------|-------------------|----------------|
| **Deals acted on** | 2-3/week | 5-8/week | Count of "Purchased" feedback |
| **Avg profit per flip** | 30 EUR | 60 EUR | Manual tracking (until inventory tracker built) |
| **Alert precision** | >50% | >80% | `GET /analytics/alert-precision` |
| **Feedback response rate** | >70% | >90% | Same endpoint |
| **Ingestion success rate** | >90% | >95% | `GET /health/ingestion` |
| **Products with high-confidence PMN** | 10+ | 40+ | `GET /health/products` filtered by confidence |
| **Active product templates** | 15-20 | 50+ | `GET /products` count |
| **Pipeline latency** (listing to alert) | <6h | <1h | Timestamp delta: observed_at to alert sent_at |
| **Weekly gross revenue** | 60-150 EUR | 300-800 EUR | Manual P&L |

### Monthly Review

| Metric | Target (Month 1) | Target (Month 6) |
|--------|-------------------|-------------------|
| Monthly gross profit | 200-600 EUR | 1000-2000 EUR |
| Monthly infra cost | <50 EUR | <100 EUR |
| Net profit (after costs) | 150-550 EUR | 900-1900 EUR |
| ROI on working capital | >5% monthly | >15% monthly |
| Time spent per week | <3h | <2h |
| Products tracked | 20 | 100+ |
| Categories with active deals | 3 | 5+ |

### Red Flags (Act Immediately If You See These)

- Ingestion success rate drops below 80% for 2+ days --> scraper is broken
- Zero alerts for 3+ consecutive days --> check product templates and thresholds
- Alert precision drops below 40% --> thresholds are too loose, tighten them
- PMN accuracy MAE exceeds 15% --> data quality issue, check sold item sourcing
- LLM cost spike (>3x normal day) --> check enrichment batch size, may be looping

---

## 9. Timeline: 8-Week Execution Plan

### Week 1: Deploy & Seed

| Day | Task | Output |
|-----|------|--------|
| Mon | Provision Hetzner VPS. Install Docker. Clone repo. Configure `.env` | Running infra |
| Tue | `make up`, `make migrate`, verify all services healthy | 5/5 services green |
| Wed | Research and create 10 product templates (electronics, watches) | 10 active products |
| Thu | Research and create 5-10 more templates (audio, gaming, photography) | 15-20 active products |
| Fri | Run first full ingestion. Review raw data. Fix any connector issues | First listings in DB |
| Sat-Sun | Let ingestion run. Review PMN computation results | PMN baselines forming |

### Week 2: Tune & First Alerts

| Day | Task | Output |
|-----|------|--------|
| Mon | Review PMN values. Do they match your market intuition? Adjust price ranges | Validated PMN |
| Tue | Configure alert rules: start conservative (margin >25%, confidence >0.6) | Alert rules active |
| Wed | Increase ingestion frequency to every 4h for top 10 products | Fresher data |
| Thu | Enable LLM enrichment. Monitor first enrichment batch | Enrichment running |
| Fri | Act on first Telegram alerts (or investigate why there are none) | **First potential deal** |
| Sat-Sun | Continue monitoring. Provide feedback on all alerts (interested/not) | Feedback loop started |

### Week 3: Optimize & First Buy

| Day | Task | Output |
|-----|------|--------|
| Mon | Review Week 2 alert quality. Adjust thresholds if needed | Tuned thresholds |
| Tue | Complete remaining M1 feedback items (Telegram webhook, precision wiring) | Full feedback loop |
| Wed | Add `words_to_avoid` for noisy products. Deactivate products with no sales data | Cleaner data |
| Thu | **Make first purchase** on a high-confidence alert | First inventory item |
| Fri | List purchased item on target platform at PMN price | First flip in progress |
| Sat-Sun | Monitor listing. Check market for similar items | Learning the game |

### Week 4: Close First Loop

| Day | Task | Output |
|-----|------|--------|
| Mon | Review alert precision for the past 2 weeks. What's working? | Data-driven tuning |
| Tue | Add 5 more product templates based on what categories perform best | 20-25 products |
| Wed | Investigate cross-platform price gaps for tracked products | Manual arbitrage insights |
| Thu | Record profit/loss on completed flips. Update alert feedback | Closed-loop tracking |
| Fri | Week 4 retrospective: Am I making money? Which products? Which platforms? | First month P&L |
| **Week 4 target:** 2-4 completed flips, 100-300 EUR gross profit, alert precision >50% |

### Week 5-6: Scale Up Ingestion

| Task | Output |
|------|--------|
| Increase ingestion to every 2h for top products | Much fresher alerts |
| Build simple inventory tracker (spreadsheet or DB table) | Track all flips |
| Add 10-15 more product templates (expand to new subcategories) | 35-40 products |
| Start weekly P&L review ritual | Financial discipline |
| **Week 6 target:** 4-6 flips/week, 200-500 EUR/week, alert precision >65% |

### Week 7-8: Accelerate

| Task | Output |
|------|--------|
| Implement product auto-discovery (analyze sold data for high-spread products) | Automated product research |
| Add price trend alerting (PMN shift notifications) | Buy-the-dip signals |
| Reach 50+ active products | Broad coverage |
| Fine-tune LLM enrichment prompts based on 6+ weeks of data | Better scoring |
| Implement cross-platform spread detection | Explicit arbitrage signals |
| **Week 8 target:** 5-8 flips/week, 400-800 EUR/week, alert precision >75%, 50+ products |

---

## 10. Investment Needed

### Upfront (One-Time)

| Item | Cost | Notes |
|------|------|-------|
| VPS setup time | 4h of your time | Deploy, configure, verify |
| Product research | 8h of your time | The most important investment |
| eBay Developer account | Free | Already have `EBAY_APP_ID` configured |
| Telegram bot | Free | Already configured |
| Gemini API key | Free to start | Google AI Studio free tier: 1500 req/day |
| Working capital for first flips | 500-1000 EUR | Recoverable on resale |
| **Total upfront cash:** | **~500-1000 EUR** | Mostly working capital |

### Monthly (Recurring)

| Item | Month 1 | Month 3 | Month 6 |
|------|---------|---------|---------|
| VPS (Hetzner CX31) | 15 EUR | 15 EUR | 25 EUR (if upgraded) |
| Gemini API | 5-20 EUR | 30-60 EUR | 60-120 EUR |
| Residential proxies (if needed) | 0 EUR | 5-10 EUR | 10 EUR |
| Object storage (backups) | 2 EUR | 2 EUR | 2 EUR |
| **Total monthly infra** | **~22-37 EUR** | **~52-87 EUR** | **~97-157 EUR** |
| **Operator time** | 5-7h/week | 3-5h/week | 2-3h/week |

### ROI Projection

| Month | Revenue | Costs | Net Profit | Cumulative |
|-------|---------|-------|------------|------------|
| Month 1 | 200-600 EUR | 530-1040 EUR (incl. working capital) | -330 to -440 EUR | -440 to -330 EUR |
| Month 2 | 400-1000 EUR | 52-87 EUR | 350-950 EUR | 0 to 600 EUR |
| Month 3 | 600-1600 EUR | 52-87 EUR | 550-1550 EUR | 550-2150 EUR |
| Month 6 | 1000-2500 EUR | 97-157 EUR | 900-2350 EUR | 3500-8000 EUR |

**Payback period: Month 2.** Working capital is recovered as items sell. Infrastructure costs are negligible relative to margins.

---

## Bottom Line

This platform is **ready to make money**. The engineering is done. The pipeline works. The scoring is sophisticated. What's missing is:

1. **A server** (2h to set up)
2. **Good product templates** (1 day of research)
3. **Your time acting on alerts** (15 min/day)
4. **Iteration on thresholds** (weekly tuning)

Stop building features. Start using it. The first euro will teach you more than the next 100 commits.

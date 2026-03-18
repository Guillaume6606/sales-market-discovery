# Plan: Vinted Stealth System Improvement — DataDome Bypass

## Problem Statement

The baseline audit (2026-03-17) revealed Vinted scraping is fundamentally broken:

- **Search results**: Only `<head>` HTML captured — Vinted's Next.js app renders body client-side, but our Playwright/HTTP fallback returns pre-hydration HTML
- **Audit page capture**: 19/20 individual item pages fail to load — DataDome (`datadome/5.3.0/tags.js`) blocks serial requests after the first
- **Net effect**: Vinted connector yields near-zero usable data for the audit LLM judge

### Root Causes Identified

1. **DataDome bot detection** — Vinted uses DataDome, which fingerprints the browser session and blocks automated serial requests. Our current stealth patches (WebGL, Canvas, Audio, Plugins) don't address DataDome's behavioral and cookie-based detection.

2. **No session continuity** — Each `ScrapingSession` creates a new Playwright context. DataDome issues a `datadome` cookie on the first request; subsequent requests without it trigger a challenge or block.

3. **No navigation chain** — Requests go directly to deep URLs (item pages) without a referral chain. DataDome flags this as bot behavior (humans browse search → item, not directly to items).

4. **Insufficient JS hydration wait** — `wait_until="domcontentloaded"` + 2s sleep isn't enough for Vinted's Next.js RSC hydration. The `networkidle` wait added in the audit fix helps but doesn't address the blocked-by-DataDome case.

5. **HTTP fallback is useless for Vinted** — CloudScraper handles Cloudflare, not DataDome. The HTTP fallback returns the server-rendered shell (just `<head>` + script tags).

---

## Solution Architecture

Three-phase approach: (A) DataDome cookie persistence, (B) navigation chain + behavioral realism, (C) audit capture integration.

---

## Phase A: DataDome Cookie & Session Persistence

### Step A1: Persistent DataDome cookie jar

**Why**: DataDome issues a `datadome` cookie on the first successful page load. All subsequent requests must carry this cookie. Currently, each `ScrapingSession` starts fresh — the cookie is lost.

**File**: `libs/common/scraping.py`

**Changes**:
1. Add a module-level cookie persistence path constant:
   ```python
   VINTED_COOKIE_PATH = Path("/tmp/pwuser/vinted-cookies.json")
   ```

2. After successful Playwright page load, save cookies:
   ```python
   cookies = await self._playwright_context.cookies()
   VINTED_COOKIE_PATH.write_text(json.dumps(cookies))
   ```

3. On context initialization, restore cookies if file exists:
   ```python
   if VINTED_COOKIE_PATH.exists():
       cookies = json.loads(VINTED_COOKIE_PATH.read_text())
       await self._playwright_context.add_cookies(cookies)
   ```

4. Handle cookie expiry: if a request gets a 403 after sending cookies, delete the cookie file and retry with a fresh session (forces DataDome to re-issue).

**Impact**: Requests 2..N carry the DataDome cookie, avoiding re-challenges.

### Step A2: DataDome challenge detection & handling

**Why**: When DataDome blocks a request, it serves a challenge page (CAPTCHA or JS challenge) instead of a block page. We need to detect this and either wait for JS to solve it or flag it.

**File**: `libs/common/scraping.py`

**Changes**:
1. Add DataDome-specific detection patterns to `ANTIBOT_PATTERNS` or as a separate constant:
   ```python
   DATADOME_PATTERNS = re.compile(
       r"datadome|dd\.js|geo\.captcha-delivery\.com|"
       r"interstitial\?initialCid|captcha-delivery\.com",
       re.IGNORECASE,
   )
   ```

2. In `get_html_with_playwright()`, after page load, check if the page is a DataDome challenge:
   ```python
   if DATADOME_PATTERNS.search(html_content[:5000]):
       # DataDome JS challenge — wait for it to resolve
       await page.wait_for_load_state("networkidle", timeout=15000)
       # Check if we've been redirected to the real page
       html_content = await page.content()
       if DATADOME_PATTERNS.search(html_content[:5000]):
           # Still blocked — flag and save cookies for retry
           raise DataDomeBlockError(url)
   ```

3. Add specific `DataDomeBlockError` exception class for callers to handle.

**Impact**: Differentiates DataDome blocks from other failures; enables automatic retry after JS challenge resolves.

---

## Phase B: Navigation Chain & Behavioral Realism

### Step B1: Warm-up navigation before scraping

**Why**: DataDome tracks referrer chains. Going directly to `/items/12345` without first visiting `/catalog?search_text=...` is a strong bot signal. Humans navigate: homepage → search → item.

**File**: `ingestion/connectors/vinted.py`

**Changes**:
1. Add a `_warmup_session()` method to `VintedConnector`:
   ```python
   async def _warmup_session(self, session: ScrapingSession) -> None:
       """Visit Vinted homepage to establish a DataDome session."""
       await session.get_html_with_playwright(self.BASE_URL)
       await asyncio.sleep(random.uniform(1.5, 3.0))
   ```

2. Call `_warmup_session()` before the first search in `search_items()`:
   ```python
   async with ScrapingSession(scraping_config) as session:
       await self._warmup_session(session)
       html_content = await session.get_html_with_playwright(search_url)
   ```

3. For `get_item_details()`, navigate to the item page from the search results page (not directly):
   ```python
   # Navigate to search first, then click through to item
   await session.get_html_with_playwright(search_url)
   await asyncio.sleep(random.uniform(1, 2))
   html_content = await session.get_html_with_playwright(item_url)
   ```

**Impact**: Establishes a natural navigation chain; DataDome sees a real browsing session.

### Step B2: Inter-request timing jitter

**Why**: Bot detection flags uniform timing between requests. Current code uses `asyncio.sleep(2 + random.random())` which clusters around 2-3s — too regular.

**File**: `libs/common/scraping.py`, `ingestion/audit.py`

**Changes**:
1. Replace fixed delays with a configurable human-like delay distribution:
   ```python
   def human_delay(min_s: float = 2.0, max_s: float = 6.0) -> float:
       """Log-normal distribution centered around the midpoint."""
       mu = (min_s + max_s) / 2
       return min(max(random.lognormvariate(math.log(mu), 0.4), min_s), max_s)
   ```

2. Use `human_delay()` in all inter-page waits:
   - `capture_audit_batch()` between listings
   - `search_items()` if paginating
   - `_warmup_session()` → search transition

**Impact**: Timing looks like human browsing (variable, with occasional long pauses).

### Step B3: Scroll & interaction simulation on Vinted pages

**Why**: DataDome monitors mouse movement and scroll behavior. The current `_humanize_and_settle()` in `ScrapingSession` does random scrolls, but the audit capture in `audit.py` does none — it just loads and captures.

**File**: `ingestion/audit.py` — `capture_audit_batch()`

**Changes**:
1. After consent banner dismissal, add scroll/interaction simulation before capturing HTML:
   ```python
   # Simulate reading the page
   await page.mouse.move(
       random.randint(300, 800),
       random.randint(200, 400),
   )
   for _ in range(random.randint(2, 4)):
       await page.mouse.wheel(0, random.randint(300, 800))
       await page.wait_for_timeout(random.randint(500, 1500))
   ```

2. Wait for Vinted's React content to render after scroll (triggers lazy loading):
   ```python
   await page.wait_for_timeout(random.randint(1000, 2000))
   ```

**Impact**: DataDome sees mouse/scroll activity; lazy-loaded content becomes available.

---

## Phase C: Audit Capture Integration

### Step C1: Reuse ScrapingSession in audit capture

**Why**: `capture_audit_batch()` creates its own Playwright context (line 252) that doesn't use `ScrapingSession`'s consent automation, humanization, or cookie persistence. It's a stripped-down version missing critical stealth features.

**File**: `ingestion/audit.py`

**Changes**:
1. Refactor `capture_audit_batch()` to use `ScrapingSession` instead of raw Playwright:
   ```python
   async with ScrapingSession(scraping_config) as session:
       page = session._playwright_page  # Or expose a method
       for listing in listings_with_urls:
           html_content = await session.get_html_with_playwright(listing.url)
           results[listing.obs_id] = AuditCapture(
               screenshot_path=None,
               html_snippet=html_content[:50000],
           )
   ```

2. Alternatively, expose a `navigate_and_capture(url)` method on `ScrapingSession` that returns both HTML and an optional screenshot path. This keeps the session's consent handling, humanization, and cookie persistence.

3. Add a `capture_screenshot(page, path)` helper that can be called after `get_html_with_playwright()`.

**Impact**: Audit captures benefit from all stealth features; DataDome cookies are shared.

### Step C2: Batch item pages through search navigation

**Why**: Instead of visiting 20 item URLs directly (which triggers DataDome), navigate to the search page first, then visit items one by one with referrer context.

**File**: `ingestion/audit.py`

**Changes**:
1. Group listings by source domain (vinted.fr, leboncoin.fr).
2. For Vinted listings, navigate to `vinted.fr/catalog` first (warmup), then visit each item URL.
3. Set the `Referer` header to the search page URL for each item navigation:
   ```python
   await page.goto(listing.url, referer="https://www.vinted.fr/catalog")
   ```

**Impact**: DataDome sees a natural search → item browsing pattern.

### Step C3: Reduce batch size & add cooling periods

**Why**: Even with stealth improvements, hitting 20 Vinted pages in rapid succession will trigger rate limiting. Need cooling periods.

**File**: `ingestion/audit.py`, `ingestion/audit_cli.py`

**Changes**:
1. Add a configurable `max_consecutive_per_domain` parameter (default: 5).
2. After every `max_consecutive_per_domain` pages from the same domain, insert a longer cooling period (15-30s).
3. Optionally rotate between connectors (audit 5 Vinted, 5 LeBonCoin, 5 Vinted...) to spread load.

**Impact**: Avoids rate limiting by spreading requests over time.

---

## Phase D: Fallback & Resilience (Optional/Future)

### Step D1: Residential proxy rotation

**Why**: DataDome fingerprints IP addresses. A single datacenter IP making many requests is a strong signal. Residential proxies rotate IPs per request.

**Files**: `libs/common/settings.py`, `libs/common/scraping.py`

**Changes**:
1. Add proxy configuration to settings:
   ```python
   proxy_url: str | None = Field(default=None)
   proxy_rotation: bool = Field(default=False)
   ```

2. Pass proxy to Playwright context launch:
   ```python
   context = await p.chromium.launch_persistent_context(
       proxy={"server": settings.proxy_url} if settings.proxy_url else None,
       ...
   )
   ```

**Impact**: Strongest anti-detection measure, but adds cost (~$5-15/GB for residential proxies).

### Step D2: Vinted API exploration

**Why**: Vinted's mobile app uses a REST API (`api.vinted.fr`) that may be easier to access than the web scraping approach. If we can authenticate with the API directly (using a Vinted account token), we bypass DataDome entirely.

**Files**: New `ingestion/connectors/vinted_api.py`

**Research needed**:
1. Reverse-engineer Vinted mobile API endpoints (search, item details)
2. Authentication flow (OAuth2, session tokens)
3. Rate limits and terms of service

**Impact**: If viable, this eliminates the bot detection problem entirely for Vinted.

---

## Implementation Order

| Priority | Step | Effort | Expected Impact |
|----------|------|--------|-----------------|
| 1 | A1: Cookie persistence | Small | High — fixes session continuity |
| 2 | B1: Warmup navigation | Small | High — establishes referrer chain |
| 3 | C1: Reuse ScrapingSession in audit | Medium | High — audit gets full stealth |
| 4 | B2: Timing jitter | Small | Medium — reduces detection signals |
| 5 | A2: DataDome challenge handling | Medium | Medium — auto-recovery from blocks |
| 6 | B3: Scroll simulation in audit | Small | Medium — triggers lazy content |
| 7 | C2: Search-first navigation | Small | Medium — natural referrer chain |
| 8 | C3: Batch cooling periods | Small | Medium — avoids rate limits |
| 9 | D1: Proxy rotation | Medium | High — but adds cost |
| 10 | D2: Vinted API | Large | Very high — bypasses detection |

**Recommended first batch**: Steps A1 + B1 + C1 (3 changes, ~2-3 hours, highest ROI).

---

## Files Modified Summary

| File | Steps | Changes |
|------|-------|---------|
| `libs/common/scraping.py` | A1, A2, B2 | Cookie persistence, DataDome detection, human delay |
| `ingestion/connectors/vinted.py` | B1 | Warmup navigation, search-first browsing |
| `ingestion/audit.py` | B3, C1, C2, C3 | Reuse ScrapingSession, scroll sim, batch cooling |
| `ingestion/audit_cli.py` | C3 | Batch size config |
| `libs/common/settings.py` | D1 | Proxy config (future) |

---

## Verification

After implementing steps A1 + B1 + C1:

```bash
# 1. Rebuild container
docker-compose build --no-cache ingestion

# 2. Run stealth test against bot detection tester
docker-compose run --rm -e RUN_STEALTH_TEST=true ingestion

# 3. Run Vinted-only audit (small batch)
docker-compose exec -T ingestion xvfb-run -a uv run python -m ingestion.audit_cli \
  --connectors vinted \
  --products-per-connector 1 \
  --listings-per-product 5 \
  --html-only \
  --output-dir /app/reports/vinted-stealth-test

# 4. Check results
docker cp $(docker-compose ps -q ingestion):/app/reports/vinted-stealth-test/. ./reports/vinted-stealth-test/
```

**Success criteria**: Vinted audit accuracy ≥ 80% (currently 16.7-33%).

---

## Risks

| Risk | Mitigation |
|------|------------|
| DataDome updates detection | Modular stealth system — can update patches independently |
| Vinted blocks IP permanently | Proxy rotation (Step D1); rate limiting (Step C3) |
| Cookie persistence fails across container restarts | Use Docker volume for `/tmp/pwuser` instead of tmpfs |
| Legal/ToS concerns | Research Vinted ToS; consider API approach (Step D2) |
| Over-engineering stealth | Start with A1+B1+C1, measure, then add complexity only if needed |

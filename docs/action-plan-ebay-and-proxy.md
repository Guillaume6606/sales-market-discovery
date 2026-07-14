# Action Plan — eBay Production Key & Residential Proxy

**Owner:** Guillaume (procurement/signup) + Claude (code wiring)
**Status:** Pending operator signups
**Context:** The stack is deployed and producing real data from LeBonCoin + Vinted with
computed PMN. Two data-source gaps remain, tracked here. See
`docs/superpowers/specs/2026-07-01-recovery-to-first-1000-eur-design.md` (Phase 1)
for the strategic rationale.

Do the **proxy first** — 10-minute signup, unblocks LeBonCoin reliability immediately.
eBay is slower (account verification + optional Insights approval) and is primarily the
*pricing* anchor, so it can lag a day.

---

## Task 4 — Residential proxy (do first)

**Why:** LeBonCoin and Vinted use DataDome, which blocks the OVH datacenter IP.
Overnight runs show LeBonCoin at ~718 `no_data` vs 2 `success` — it only slips through
occasionally. Routing scraper traffic through French/EU residential IPs fixes this.
(The cron retry-storm that was worsening the blocking is already fixed.)

### Operator steps

1. **Pick a provider** — pay-as-you-go residential, no enterprise plan needed at this volume:
   - **IPRoyal** (`royalresidential`) — ~$1.75/GB, no minimum. *Recommended starting point.*
   - Decodo (ex-Smartproxy) or Webshare — nicer dashboards, small monthly minimums.
   - Avoid Bright Data / Oxylabs for now (overkill, higher minimums).
2. **Sign up, add a few euros** of credit. Real usage here is well under 1 GB/month.
3. **Create a rotating residential endpoint, geo-targeted to France (FR).**
4. **Copy the endpoint URL**, form: `http://USERNAME:PASSWORD@host:port`
   (IPRoyal example host: `geo.iproyal.com:12321`; France targeting is often a password
   suffix like `_country-fr`). Keep the full string.

### What you hand Claude

The single proxy URL: `http://user:pass@host:port`

### What Claude will wire (code work)

- Add `scraping_proxy_url: str | None = None` to `libs/common/settings.py` (reads
  `SCRAPING_PROXY_URL` from env).
- Wire it into **both** request paths in `libs/common/scraping.py` (currently
  `use_proxies=False` and `proxy_list=[]` are hardcoded stubs, never applied):
  - the `curl_cffi` `AsyncSession` (used by Vinted HTML + generic fetches),
  - the Playwright `launch_persistent_context` (`proxy=` arg).
- Pass an `lbc.Proxy` built from the same URL into `LeBonCoinAPIConnector` (the connector
  already accepts a `proxy=` param — just unused).
- Add `SCRAPING_PROXY_URL=` to `.env.example` and set the real value in `.env.prod`, then
  `make deploy-env && make deploy`.
- Re-run the ingestion test; LeBonCoin `no_data` rate should collapse.

**Cost:** ~2–5 €/month at this volume.

---

## Task 3 — eBay production key (Browse API)

**Why:** `.env.prod` currently holds a **sandbox** App ID, and the connector targets the
**Finding API**, which eBay decommissioned in Feb 2025 — so every eBay run returns
`no_data` (confirmed: 720/720 empty). eBay is the only source of true **sold** prices, the
proper anchor for PMN. Until this lands, PMN is built from LeBonCoin/Vinted *asking*
prices, which run slightly high.

### Operator steps

1. **Register** at [developer.ebay.com](https://developer.ebay.com) with your eBay account;
   accept the API License Agreement.
2. **Verify the account** (email link). Production keysets aren't issued until verified;
   link a registered eBay user account if prompted.
3. **Application Keysets → create a keyset under _Production_** (not Sandbox).
4. **Copy two values:**
   - **App ID (Client ID)** — replaces the sandbox `EBAY_APP_ID` in `.env.prod`
   - **Cert ID (Client Secret)** — new, needed for OAuth2
   (Dev ID and RuName aren't needed for Browse API guest access.)
5. **(Optional, for true sold prices) apply for the Marketplace Insights API** — it's the
   only API returning sold/completed listings and is **approval-gated** (days–weeks, can be
   rejected). Submit the access request describing the arbitrage/market-research use case.

### What you hand Claude

- Production **App ID** and **Cert ID**.
- Whether Marketplace Insights was granted (yes/no/pending).

### What Claude will wire (code work)

- Add `ebay_cert_id: str | None = None` to `libs/common/settings.py`.
- Rewrite `ingestion/connectors/ebay.py`:
  - fetch an OAuth2 **application access token** (client-credentials grant) and cache it,
  - replace Finding API calls with **Browse API**
    `GET /buy/browse/v1/item_summary/search` (marketplace `EBAY_FR`) for active listings,
  - map the Browse response to the existing `Listing` contract (the 26 parser unit tests
    define expected field semantics — keep them green).
- **Sold data**, in order of preference:
  1. Marketplace Insights API if granted;
  2. else scrape eBay sold/completed search (`LH_Sold=1&LH_Complete=1`) via the existing
     stealth stack (+ proxy from Task 4);
  3. else fall back to active-listing statistics for PMN with margin thresholds raised
     ~10 pts (documented in the Phase 1 spec).
- Update `.env.prod` (`EBAY_APP_ID`, `EBAY_CERT_ID`), `make deploy-env && make deploy`,
  re-run the eBay ingestion and confirm non-empty results + a fresh audit.

**Cost:** free (Browse API has a generous call allowance).

---

## Sequencing summary

1. Proxy signup (you) → hand over URL → Claude wires + deploys → LeBonCoin reliable.
2. eBay production keyset (you) → Claude can start the Browse API rewrite in parallel
   (doesn't need the key until the final deploy/test) → hand over keys → deploy → true
   sold-price PMN.

# Review eBay.fr sold-comparable proposals

Sold comps pre-fill **proposals** for a verified valuation reference from eBay.fr completed sales. A proposal never authorizes an alert: the alert gate (`evaluate_valuation`) reads only `verified_valuation_reference`, which is created when an operator approves a proposal. The observed / estimated / measured semantics in `profit-workstreams-operations.md` are unchanged: completed-sale prices are **observed**; the approved reference is an **estimate** under operator assumptions.

## Terms of use and frequency

Automated collection of eBay pages is **not covered by eBay's terms of use**. The feature is off by default (`SOLD_COMPS_ENABLED=false`); the operator decides whether to enable it. Frequency is deliberately low:

- at most one search page per active product per `SOLD_COMPS_REFRESH_DAYS` (default 7, minimum 7);
- at most `SOLD_COMPS_MAX_REQUESTS_PER_RUN` pages per run (default 3), spaced by at least `SOLD_COMPS_MIN_DELAY_SECONDS`;
- one attempt per page, no retry; any denial (403/429/challenge) or HTTP error starts a `SOLD_COMPS_COOLDOWN_SECONDS` cooldown (default 24 h) for source `ebay_sold`, and the run stops.

The ARQ job `refresh_sold_comps` is scheduled once a day; most days it does nothing because products are not due.

## What a proposal contains

One proposal per (product, normalized condition) per refresh:

- **Sales kept**: title matches the product identity using the valuation gate's own checks (relevance classification, required tokens = product search query, excluded tokens = single-word `words_to_avoid`; multi-word avoid phrases are applied to titles but not copied into the reference), sale date known, within `SOLD_COMPS_WINDOW_DAYS` (default 90) and not after the decision time, condition recognised by `normalize_condition`.
- **Sales excluded** (kept for audit with a reason): best offer accepted or struck-through price (the displayed price is not the final price), price ranges, non-EUR prices, unknown date or condition, wrong variant/accessory/parts, and any eBay item already observed by this system for the product (prevents a listing from valuing itself).
- **Statistics**: median, P25 and P75 of item prices **excluding shipping**, sale count and date window. Shipping is recorded per sale only when stated explicitly ("Livraison gratuite" = 0; unstated = unknown).
- **Status**: `pending` when sale count ≥ `SOLD_COMPS_MIN_COMPARABLES` (default 8), otherwise `insufficient` (cannot be approved). A newer refresh marks older `pending`/`insufficient` proposals for the same product and condition `rejected` by `system`.

## Operator workflow

1. Open **Sold Comps** (`ui/pages/9_Sold_Comps.py`), filter `pending`.
2. Inspect the source and excluded sales; open the eBay search link to spot-check prices against the page.
3. **Approve**: choose purchase source and resale destination, expiry, limitations and every fee, charge, logistics and risk input (zero is an explicit assumption). The reference is created at the median price, `comparable_sold_at` = most recent comparable sale, `reviewed_at` = approval time, evidence URL = the search page. Tokens and condition come from the proposal and cannot be edited; if they are wrong, reject and enter a manual reference in **Valuation**.
4. **Reject** with a reason when the comparables are not the same product, condition or market.

API: `GET /sold-comps/proposals?status=pending`, `GET /sold-comps/proposals/{id}`, `POST /sold-comps/proposals/{id}/approve`, `POST /sold-comps/proposals/{id}/reject`.

## Known limits

- The parser was built against a hand-made fixture: the live capture was refused (HTTP 403). Its accuracy on real eBay HTML is **not measured**. If eBay changes its markup, the page is reported `unrecognized_page` and no proposal is written.
- Access from the production host is not verified.
- Best-offer sales are dropped, which can bias the median upward for items that commonly sell by offer.
- Median of completed eBay sales is an exit estimate for eBay; using it for another destination is an operator decision.

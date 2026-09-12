# Provider extraction and delivery to France

This release improves source access and excludes accessory noise from ordinary device opportunities. It does not certify authenticity or guarantee profit.

## Operator workflow

1. Apply migration `0011_france_delivery` before starting the new backend/worker/UI images. Keep the deployed Compose project and environment. This migration adds nullable delivery evidence and review snapshot fields; historical delivery is unknown, not inferred from a French URL or EUR price.
2. In Products, use the exact target model/edition/storage in the product name. Broad search queries may discover candidates, but title classification checks the target. Cash Converters is now available as a purchase provider; it is not a verified sold-price source or a supported resale destination.
3. In Listings and Valuation, inspect candidates. A normal device bundled with accessories can pass, but accessory-only, empty packaging, broken/parts-only, wrong variants and ambiguous lots are excluded from ordinary opportunities. Accessories such as lenses can be explicit target products themselves. Existing observations are checked again during valuation; no destructive purge is required.
4. Record a sold comparable and actual cost assumptions. For missing shipping or delivery, open the listing and confirm delivery to your own French address, then save a time-limited review in Valuation with evidence. Do not use zero shipping to force eligibility.
5. Configure actual working capital and an alert rule. France eligibility, known shipping, reviewed value, freshness, relevance and capital are all required before an alert. Retained raw search results are not purchase recommendations.

## Delivery scope

`delivery_to_france` is true, false, or unknown, with separate evidence. Unknown or false blocks valuation. A current manual review can establish eligibility, but is tied to the listing URL and raw fields. Ingestion permanently deactivates existing reviews after any meaningful observation change, including a change that later reverts. Reviews also expire.

Country-level evidence is not a postcode-specific checkout promise. Cash Converters' published delivery policy concerns metropolitan France; confirm Corsica/overseas and any other exceptions directly at checkout. No shipping postcode was invented or stored for you.

- eBay search requests `deliveryCountry:FR` and fixed-price offers. A shipping quote is used automatically only if it is fixed, EUR-denominated and explicitly estimated for France. Otherwise shipping remains unknown pending review.
- Cash Converters emits individual store offer IDs and prices, never a category minimum. Delivery text applies only to the selected offer or an option carrying its own delivery statement. Other offers remain unknown. Checkout-only shipping fees remain unknown.
- LeBonCoin and Vinted delivery is unknown unless explicit evidence is available through a review. A French website, seller location, shipping price or item currency alone does not prove delivery to France.

## Reliability and interpretation

Vinted uses the existing configured proxy, French bootstrap headers, bounded retries for transient failures, and a process-wide cooldown after 401/403/429. A later retry may recover, but changing a header cannot guarantee uninterrupted marketplace access. Cooldowns do not survive process restarts or coordinate separate workers.

Cash Converters discovers its public storefront search configuration and resolves product families to individual offers. Search/schema/detail failures raise an error rather than masquerading as no results. Requests are bounded; the current connector does not create accounts or buy anything. Easy Cash was not added because the probe was blocked.

The dashboard now labels ingestion as healthy/degraded/unknown separately from application readiness. Fetch success includes completed searches with no retained matches; running, skipped and unsupported jobs are excluded. That rate measures transport outcome, not precision or profitable sourcing yield.

LLM classification uses temperature zero and structured categories. Missing/invalid classification and request failures do not pass automatically. Existing per-product LLM settings remain in effect. No LLM cost/latency benchmark or marketplace precision estimate is claimed by the synthetic regression tests.

## Evidence and limitations

- Measured live from VPS: candidate Vinted connector returned two parsed GoPro search results using the existing proxy, loaded only in a diagnostic process. No deployed files or production records changed.
- Measured provider probes: Easy Cash access returned HTTP403; Cash Converters returned three individual offers in both local and VPS in-memory read-only probes (two with delivery evidence, one unknown). Availability and deal margins vary; these observations are not a sell-through study.
- Automated regression tests cover accessories, variants, bundles, failure handling, delivery evidence, review expiry/invalidation and migration mechanics. Real-world precision/recall remains unmeasured; label a fresh sample before tuning further and retain a separate untouched evaluation set.
- A known professional store and published protections reduce some uncertainty, but do not prove an individual item's authenticity, condition, account-unlocked status or resale value. Use the invoice, item details and actual checkout terms. Consumer guarantees may differ for professional resale purchases.

Sources checked September 12, 2026:
- eBay destination filters: https://developer.ebay.com/api-docs/buy/static/ref-buy-browse-filters.html
- eBay shipping context: https://developer.ebay.com/api-docs/buy/api-browse.html
- Cash Converters terms: https://www.cashconverters.fr/conditions-generales-de-vente
- Cash Converters storefront: https://www.cashconverters.fr/
- Easy Cash catalogue probe: https://bons-plans.easycash.fr/multimedia/appareil-photo

## Release verification

Measured on September 12: 406 unit/UI tests and 28 PostgreSQL integration tests passed. The integration suite includes blank → 0011 → 0010 → 0011 migration checks and review expiry/change/reversion regressions. Ruff check and format check passed across 142 Python files; compilation and diff checks passed. No production deployment was performed.

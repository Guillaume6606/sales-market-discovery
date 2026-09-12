# Provider quality and France delivery implementation

User approved implementation after provider comparison; France delivery is required.

- [x] Vinted: configured proxy, bounded transport/session retries, sanitized status errors, process cooldown; live extraction must be verified separately.
- [x] Precision: explicit target-device classification, retain device bundles, reject accessory-only/parts/wrong variants/mixed lots; LLM uncertainty must not pass silently. Reapply to existing valuation candidates.
- [x] Provider: probe Easy Cash and Cash Converters; implement Cash Converters only if individual offers/availability are extractable. No category minimum prices as offers. France availability must have evidence.
- [x] Delivery: nullable raw France eligibility/evidence, migration0011, immutable operator review and snapshot invalidation, eBay France search filter, fail-closed valuation.
- [x] Health: distinguish degraded ingestion from app outage, completed fetch outcomes from filtered inventory yield.
- [x] Verify focused/unit/PostgreSQL tests, migrations, live read-only probes; preserve local user configuration and README modifications. No deployment or messages.

Rulings: work on dedicated branch feature/provider-quality-france in existing workspace; retain user uncommitted files. France means metropolitan delivery evidence, with exact postcode/checkout verification still required. No blanket scam-free claim. No added paid subscription/API purchases. Live403 remains a measured access limitation, not a successful connector result.

Verification September 12: measured 406 unit/UI tests and 28 PostgreSQL integration tests passed; Ruff check/format across 142 files, compileall and diff check passed. Migration blank->0011->0010->0011 tested in isolated schemas. Candidate Vinted VPS probe returned 2 listings; CashConverters local/VPS probe returned 3 offers. Independent review issues (URL/reversion invalidation, variants before bundles, broad query discriminators, empty packaging, shipping context, provider failure handling) resolved with regressions. No deployment, production writes, messages or purchases.

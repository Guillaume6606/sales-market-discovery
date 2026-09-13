# Historical V2 evaluation — September 12, 2026

This records the earlier evidence-based contract and smoke tests. For the simplified seven-field contract, expanded model matrix and current limitations, see the [V3 benchmark](listing-vision-benchmark-v3.md). V3 has assistant-assessed development/selection measurements; the four-finalist holdout is complete; see the current report.

Measured September 12, 2026:

- Baseline: 406 unit tests passed before changes.
- Final implementation after live-protocol fixes: 480 tests passed (446 unit and 34 PostgreSQL integration) across `tests/unit/` and `tests/integration/`, with PostgreSQL tests using isolated schemas on the disposable local container. No tests in those directories were skipped. Existing dependency deprecation warnings remain.
- Ruff check and formatting check passed; `git diff --check` passed.
- Alembic upgrade through `0012_listing_vision`, downgrade to `0011_france_delivery`, and re-upgrade passed in a separate temporary PostgreSQL schema.
- Review findings covering shadow isolation, unknown variants, claim recovery, billing reconciliation and frozen evaluation photographs were fixed and re-reviewed.

The test suite verifies input bounds, public-IP pinning, redirect rejection, image decoding, strict structured outputs, cache invalidation, retry accounting, concurrent budget reservations, ownership fencing, reconciliation, shadow behavior, France-delivery gates and frozen-image evaluation integrity. Mocked responses test integration behavior, not model accuracy.

A read-only production export yielded 93 unlabeled listings with photographs: 72 LeBonCoin and 21 eBay. They are private evaluation inputs under gitignored `data/vision-eval/`, not an out-of-sample benchmark. The proposed 250 human-labeled development/holdout examples have not been assembled. No labels have been generated from model predictions.

The user authorized a three-listing photo/text comparison capped cumulatively at $0.50. Both current models returned validated extraction for 3/3 identical frozen inputs from the VPS after two protocol fixes: use the SDK response_json_schema field, and express mutually exclusive image/text evidence in the schema and prompt. Schema/prompt versions were bumped to invalidate earlier cache entries. These are development samples used to diagnose the protocol, not an untouched holdout.

| Model | Measured valid responses | Measured latency range | Derived cost for three calls | Derived cost / 1,000 similar calls |
| --- | --- | --- | --- | --- |
| gemini-3.1-flash-lite | 3/3 | 1.59–2.02 s | $0.004568 | $1.52 |
| gemini-3.5-flash-lite | 3/3 | 1.99–3.07 s | $0.0070298 | $2.34 |

Costs use reported token counts and published standard rates, not an invoice. The final six calls total $0.01159730. Including the first VPS comparison and minimal connectivity probe, known-usage calls total $0.02213225. Conservatively retaining all unknown-usage failed-call reservations gives a cumulative ceiling of $0.28234825, below the authorized $0.50. Private raw records are in data/vision-eval/vps-comparison-v2.json.

Local calls failed due to API location restrictions; the deployed VPS succeeded. Gemini 2.5 Flash-Lite also returned an unavailable-to-new-users error. The vision default is now the tested gemini-3.1-flash-lite, provisionally selected for lower observed cost; this does not prove superior quality. Existing environment overrides and production deployment remain unchanged. Legacy enrichment/relevance model availability still needs separate verification before activation.

Accuracy, scam detection reliability, and improvement over the text-only baseline remain unmeasured. Next: label and freeze a development/holdout corpus, compare the baseline and challengers, then evaluate the untouched holdout. Scaleway requires its API credential; local inference requires a running pinned model endpoint. No VPS RAM upgrade is justified by this hosted smoke test.

Rates: https://ai.google.dev/gemini-api/docs/pricing

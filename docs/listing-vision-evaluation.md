# Offline validation passes; model quality is not yet measured

Measured September 12, 2026:

- Baseline: 406 unit tests passed before changes.
- Final implementation: 477 tests passed across `tests/unit/` and `tests/integration/`, with PostgreSQL tests using isolated schemas on the disposable local container. No tests in those directories were skipped. Existing dependency deprecation warnings remain.
- Ruff check and formatting check passed; `git diff --check` passed.
- Alembic upgrade through `0012_listing_vision`, downgrade to `0011_france_delivery`, and re-upgrade passed in a separate temporary PostgreSQL schema.
- Review findings covering shadow isolation, unknown variants, claim recovery, billing reconciliation and frozen evaluation photographs were fixed and re-reviewed.

The test suite verifies input bounds, public-IP pinning, redirect rejection, image decoding, strict structured outputs, cache invalidation, retry accounting, concurrent budget reservations, ownership fencing, reconciliation, shadow behavior, France-delivery gates and frozen-image evaluation integrity. Mocked responses test integration behavior, not model accuracy.

A read-only production export yielded 93 unlabeled listings with photographs: 72 LeBonCoin and 21 eBay. They are private evaluation inputs under gitignored `data/vision-eval/`, not an out-of-sample benchmark. The proposed 250 human-labeled development/holdout examples have not been assembled. No labels have been generated from model predictions.

A six-call Gemini smoke comparison on three listings was prepared, capped at $0.50, but automatic approval review rejected sending listing photos/descriptions to Google without explicit permission. No paid call ran. Live provider success rate, photo interpretation quality, latency, actual invoice cost, and differences between Flash, Flash-Lite, Scaleway and local inference remain unmeasured. No model winner or production activation is claimed.

Next validation: approve the scoped live test, confirm actual image-bearing responses, label and freeze the evaluation corpus, compare the text-only baseline and hosted challengers, then evaluate the untouched holdout. Scaleway requires its API credential; local inference requires a running pinned model endpoint. Hardware upgrades remain unnecessary until a local benchmark establishes a benefit.

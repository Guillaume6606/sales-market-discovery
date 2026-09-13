# Listing photographs now have a separate factual extraction path

The implementation is opt-in. `VISION_ENABLED=false` preserves the existing ingestion path. Enable shadow mode first to collect factual results without replacing the existing LLM filter, enrichment or financial eligibility. Vision cannot confirm authenticity, payment protection or delivery to France.

## Run on the existing VPS

Deploy the feature code and migration `0012_listing_vision` before starting workers with this feature. No new hardware is needed for a hosted provider. Add these non-secret settings to the deployment environment:

```dotenv
ENRICHMENT_LLM_MODEL=gemini-2.5-flash-lite
VISION_ENABLED=true
VISION_SHADOW_MODE=true
VISION_PROVIDER=gemini
VISION_MODEL=gemini-3.1-flash-lite
VISION_MONTHLY_BUDGET_USD=10
VISION_MONTHLY_BUDGET_EUR=10
```

Gemini uses the existing `GEMINI_API_KEY` or configured Vertex credentials. Scaleway uses `SCALEWAY_API_KEY`, `VISION_PROVIDER=scaleway` and `VISION_MODEL=mistral-small-3.2-24b-instruct-2506`. Unknown provider/model price schedules fail closed. Pixtral, Gemma 4, Qwen 3.6, Qwen 3.5 and Mistral Medium are supported for evaluation with explicit price schedules. Pixtral is comparison-only while its announced October 1 retirement remains in effect.

Use the existing deployment commands to rebuild, apply `uv run alembic upgrade head`, and recreate backend/ingestion/UI services. A code deployment without environment changes leaves vision disabled. The existing enrichment trigger runs vision too; product pipelines also run a bounded vision batch after detail fetching. Only fresh active listings are selected. Cash Converters currently lacks detail fetching in the product pipeline, so its photographs are not yet available to this job.

Inspect `/health/vision` or the **Photo analysis** block in Health, and **Photo and description analysis** in Listing Explorer. Health distinguishes USD and EUR and reports both settled charges and conservative outstanding reservations. Shadow mode adds evaluation calls to the legacy workload; its cap does not cover existing legacy LLM calls.

After evaluating labeled examples, `VISION_SHADOW_MODE=false` activates vision gating and replaces the legacy filtering/enrichment calls. Bounded ambiguous active candidates can reach detail fetching. Sold records still require existing exact-product and verified-sale checks. Pending/failed/text-only results, uncertain identity, detected accessories, defects and contradictions cannot promote an alert. Bundles remain review candidates and are blocked from automatic valuation until a separate bundle-aware reference workflow is available. Existing France delivery, shipping cost, reviewed reference and capital gates still apply.

## Budget and cache behavior

Requests are keyed by provider/model, prompt/schema, target, text and normalized image content hashes. A current per-listing result is reused for at most 24 hours, unless text, photo URLs, target or model configuration change first. Identical refreshed image bytes reuse the durable inference cache. Changed bytes at an unchanged URL are detected at that refresh, not continuously.

Each provider attempt gets an atomic reservation before the request; SDK retries are disabled and explicit retries reserve separately. Unknown billed usage retains the reservation. PostgreSQL ownership leases prevent duplicate workers from committing an old result; interrupted claims become reclaimable after ten minutes. Completed errors retry after a five-minute cooldown. Hosted concurrency is two per worker process, local concurrency one; the budget is shared across workers.

The current conservative reservation covers 65,536 input tokens and the configured maximum output. Inputs are bounded to 32 KB text and at most three resized photographs. Actual provider usage replaces the reservation when reported. This is conservative accounting based on the supported model schedule, not a provider-side spending cap; reconcile unknown amounts against billing. An operator can call `VisionStore.record_attempt(attempt_id, metadata, actual_decimal_cost)` after checking the provider invoice. It permits unknown-to-known reconciliation once, in the original currency and billing month; do not guess zero for failed requests.

## Evaluate without contaminating the holdout

Keep manifests, photographs and raw model outputs in gitignored `data/vision-eval/`. Export is read-only:

```sh
uv run python -m scripts.evaluate_listing_vision export --output data/vision-eval/manifest.jsonl --limit 250
uv run python -m scripts.evaluate_listing_vision run --manifest data/vision-eval/manifest.jsonl --output data/vision-eval/flash-lite.json --split dev --limit 100 --budget 5
```

The runner uses the configured database's durable cache/budget tables. Prefer an isolated evaluation database; it reduces the configured currency limit to at most five for that run, never grants additional production allowance. The limit is per currency, not an implicit exchange conversion.

Each JSONL record contains `id`, `group_id`, `split`, `source`, `target`, `title`, `description`, `photo_urls` and `expected`. Exported `expected` is null and every record begins in `dev`. An assessor fills `expected` with the seven V3 fields, using null for unverifiable fields and recording assessor provenance. Assistant-assessed references are not independent human ground truth. Development, selection and holdout splits are supported. Group relistings and duplicate images before assigning holdout; supply `image_hashes` for content duplicate checks. Shared groups, URLs or supplied image hashes across splits are rejected. Missing image hashes mean content-level duplicate leakage has not been checked.

Example label fragment:

```json
{"item_class":"accessory","model":null,"variant":null,"included_accessories":["controller"],"seller_reported_faults":[],"visible_damage":null,"text_photo_conflict":null}
```

The first vision evaluation freezes normalized photographs beside the manifest in `<manifest-stem>-images/`. Later models consume those exact bytes, with SHA-256 verification on every run. All manifest images are checked for actual content duplication across splits. Optional manifest `image_hashes` must match the frozen bytes. A failed initial download freezes an empty image set too: start a new, explicitly named manifest/snapshot to retry it rather than silently changing comparison inputs. Do not run snapshot creation concurrently.

Use `--text-only` to establish a matched-model baseline, then compare models with the same prompt and inputs. The report includes field precision/recall, classification deltas against the deterministic heuristic, confusion counts, abstention, latency, currency-separated known costs, unknown charges, and provider/product slices. Bootstrap intervals are reproducible; tiny or homogeneous samples do not establish high precision. No-label runs are smoke tests and explicitly report accuracy as unmeasured. The held-out runner refuses unlabeled records.

## Local model option

Use a separately managed llama.cpp server with a pinned runtime image/build, a pinned `ggml-org/SmolVLM2-2.2B-Instruct-GGUF` artifact and its matching projector. Set `VISION_PROVIDER=local`, `VISION_MODEL` to the served identifier, `VISION_LOCAL_MODEL_REVISION` to the actual artifact revision, and `VISION_LOCAL_BASE_URL` to its internal OpenAI-compatible endpoint. Model files and runtime provisioning are deliberately separate from the application images. The application does not download models or buy a larger VPS.

Benchmark peak memory, image-processing latency and application responsiveness before selecting an OVH upgrade. The September benchmarks measure Apple M5 Metal performance; this does not measure CPU-only OVH performance. No local model is automatically promoted from a runtime smoke test.

## V3 uses a directive prompt and seven flat fields

The model returns `item_class`, `model`, `variant`, `included_accessories`, `seller_reported_faults`, `visible_damage` and `text_photo_conflict`. It does not generate evidence objects, unknown-field lists or explanations. Lists contain at most six short phrases. Null means unassessed; an empty damage list does not certify working condition. Defaults: 512 output tokens, temperature zero, `VISION_RESPONSE_MODE=json_schema`. Use explicit `json_object` only for adapter comparisons. Supported Scaleway reasoning models use `reasoning_effort=none`; Gemini 3 uses minimal thinking.

V2 records remain readable for history but are stale for current gates. Enforced V3 checks configured identity, photo assessment, faults, conflicts and bundles. A deterministic text rule also blocks explicit seller declarations of non-authentic goods in enforced mode; it is not general scam detection. Shadow behavior remains non-intervening.

For a database-independent resumable benchmark, use `uv run python -m scripts.benchmark_listing_vision --help`. Required arguments: `--manifest`, `--images`, `--output`, `--ledger`, `--provider`, `--model`, `--split`. Run with `--dry-run` first. Keep the same private ledger across models and splits. Caps default to $5 and €10; cached errors are not silently retried. Native Ollama additionally requires `--local-digest` and supports `--local-url`. Keep original listing text, photos, outputs, labels and the ledger gitignored.

Current measurements and limitations: [V3 benchmark](listing-vision-benchmark-v3.md), [contract/prompt ablation](listing-vision-development-ablation-v3.md), and [development error analysis](listing-vision-development-error-analysis-v3.md). Local selection, its frozen holdout and repeated timing measurements are complete. Hosted selection/holdout and controlled hosted timing measurements remain pending.

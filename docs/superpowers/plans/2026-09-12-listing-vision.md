# Listing vision implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Checkboxes track execution, not approval.

**Goal:** Identify the actual item, variant, bundle contents and visible defects from listing photos and text, reducing accessory noise without pretending to authenticate merchandise.

**Architecture:** One cached factual extraction per changed listing, behind a small provider adapter. Keep marketplace collection, France delivery verification and financial calculations deterministic. Start with hosted inference; evaluate a quantized local model separately before buying capacity.

**Tech stack:** Existing Python, Pydantic, PostgreSQL, ARQ and Google GenAI stack; Scaleway OpenAI-compatible HTTP API; optional llama.cpp CPU server.

**Spec:** User requirements in this task: lightweight vision, descriptions and listing information, hosted/self-hosted cost comparison, France delivery, fewer accessory/bundle false positives. This document includes the design and implementation sequence.

## Implementation record — September 12, 2026

Implemented the supported enrichment default, strict factual schema, bounded secure image preparation, hosted/local adapters, durable cache leases, per-attempt currency budgets and reconciliation, separate shadow storage, enforced eligibility gates, UI/health output and frozen-input evaluation tooling. See `docs/listing-vision.md` for operation.

The live database export produced 93 unlabeled listings with photographs, stored privately under gitignored `data/vision-eval/`. This is smaller than the proposed 250-example evaluation and contains no human ground truth. No precision improvement or model winner is claimed. The user subsequently authorized the live Gemini comparison: current 3.1 and 3.5 Flash-Lite each passed 3/3 structured-response smoke cases from the VPS. See docs/listing-vision-evaluation.md for cost, protocol fixes and limitations. The vision default is now gemini-3.1-flash-lite; the historical model estimates below are superseded for that selection. Scaleway credentials and a local model server have not been configured. Production has not been deployed or activated by this implementation.

Rulings: shadow mode must preserve baseline filtering/enrichment, so vision results have separate observation columns. Enforced mode conservatively requires a nonblank variant and blocks bundles for review. CPU model provisioning and hardware purchase remain optional until measured need. Manual labels and the held-out evaluation remain outstanding, not replaced with model-generated labels.

## Decision and verified baseline

Recommend `mistral-small-3.2-24b-instruct-2506` on Scaleway as the initial challenger; compare `gemini-2.5-flash-lite` before selecting the production model. Keep the existing VPS initially.

Measured on the live VPS, September 12: relevance is configured for `gemini-2.5-flash`; enrichment resolves to `gemini-2.0-flash`; both are enabled; screenshots are disabled. This verifies configuration, not successful inference. Google lists June 1, 2026 as the Gemini 2.0 Flash shutdown date, so replacing that enrichment configuration is the first implementation task.

Code inspection: `libs/common/llm_service.py` can accept screenshot bytes, but the production setting disables that path. `ingestion/enrichment.py` sends `contents=[prompt]`; `ingestion/enrichment_prompt.py` turns photo URLs into a count rather than supplying photographs. The existing models being multimodal does not mean the application currently uses their vision capability.

Measured VPS snapshot: 2 vCPU, 3,831 MiB RAM, 2,610 MiB available, no swap. This is an idle snapshot, not peak memory capacity. Local vision latency and extraction precision are unmeasured.

Read `docs/milestone-1-todo.md`: its unchecked boxes are not reliable evidence of missing code; existing tests and ingestion/health features already exist. Reuse current facilities rather than rebuilding that milestone.

## Costs, checked September 12, 2026

Derived scenario: each uncached listing consumes 6,000 billed input tokens, including images, and 400 billed output tokens; 30 days/month. Actual identical images produce different token counts across models. These are normalized workload estimates, not measured invoice predictions. Taxes, credits, retries, downloads and the existing VPS bill are excluded; currencies remain separate.

| Option | Published input/output per million tokens | Derived 100 listings/day | Derived 1,000 listings/day |
|---|---:|---:|---:|
| Scaleway Mistral Small 3.2 | €0.15 / €0.35 | €3.12/month | €31.20/month |
| Scaleway Pixtral 12B | €0.20 / €0.20 | €3.84/month | €38.40/month |
| Gemini 2.5 Flash-Lite | $0.10 / $0.40 | $2.28/month | $22.80/month |
| Gemini 2.5 Flash | $0.30 / $2.50 | $8.40/month | $84/month |

Scaleway lists Pixtral end-of-life on October 1, 2026 and Mistral Small 3.2 as its replacement. Do not start a new Pixtral dependency. Hosted Mistral Small is larger than a tiny local model, but imposes no model RAM requirement on the VPS.

Published OVH France starting prices for whole new plans, excluding tax: 8 GB/4 vCore €7.21/month; 12 GB/6 vCore €10.40/month; 24 GB/8 vCore €19.96/month. These are advertised starting prices, not the upgrade delta or a renewal quote for the current contract. Check the account's actual upgrade offer, region and commitment before ordering.

Self-host candidate: `HuggingFaceTB/SmolVLM2-2.2B-Instruct`, quantized through `ggml-org/SmolVLM2-2.2B-Instruct-GGUF`, with its matching multimodal projector. llama.cpp documents this combination and CPU image processing. Estimated capacity target: 8 GB for an experiment, 12–16 GB preferable when sharing with the app. RAM alone does not improve CPU inference speed. Do not install the model in the current worker process; use a separate limited service, concurrency one. French model/variant recognition must be evaluated, not assumed.

Dedicated Scaleway L4 infrastructure starts at €0.93/hour: derived €678.90 for 730 hours, before tax. Model compatibility is separate; this is not a quote for managed Pixtral on L4. An always-on GPU is disproportionate to the current profit objective.

Derived break-even example: an extra €10/month for CPU hosting equals roughly 9,600 Mistral analyses at the normalized assumptions, before maintenance and quality differences. The actual upgrade delta and workload are unknown.

## Global constraints

- No purchase, VPS upgrade or deployment is performed by creating this plan.
- Never include credentials in code, fixtures, reports or commits.
- Preserve France delivery evidence gates. French text, a French domain and product photos cannot establish shipping eligibility.
- Separate seller claims from visible evidence. No model score constitutes an authenticity or scam guarantee.
- Do not use estimated resale value or PMN as evidence of product identity.
- Keep existing product and financial gates; uncertain extractions are reviewable and cannot promote an alert.
- Pin provider model ID, local artifact revision, prompt version and schema version. Temperature zero; record any unavoidable provider nondeterminism.

## Task 1: Fix the retired model and establish a baseline

**Files:** `libs/common/settings.py`, `ingestion/enrichment.py`, `tests/unit/test_enrichment_prompt.py`, new `scripts/evaluate_listing_vision.py` and private evaluation manifests outside git.

- [ ] Add a configuration test that the enrichment default is a supported model; replace `gemini-2.0-flash` with `gemini-2.5-flash-lite`. Preserve an explicit environment override and flag retired identifiers in health output. Do not claim the change has reached the VPS before deployment verification.
- [ ] Assemble an estimated 150 development and 100 untouched held-out listings, stratified by provider and product. Include PS5 shells/controllers, GoPro mounts, actual bundles, broken units, variants, ambiguous French descriptions and missing images. Store downloaded inputs privately with hashes.
- [ ] Group duplicates/relisted photos across splits and keep held-out examples out of prompts. Record selection seed and collection dates. Label exact model/variant, item class, visible defects, included accessories and unknown fields. Estimated human labeling effort: 4–6 hours.
- [ ] Report current heuristic and Gemini text-only baselines. Categorize about 20 development failures before modifying prompts. Existing synthetic relevance tests remain regressions, not out-of-sample evidence.
- [ ] Run focused existing tests with `uv run pytest tests/unit/test_enrichment_prompt.py tests/unit/test_llm_service.py -q`; commit the supported-default change independently.

## Task 2: Define factual outputs and securely prepare photographs

**Files:** new `libs/common/vision_schema.py`, `ingestion/listing_images.py`, `tests/unit/test_listing_images.py`, `tests/unit/test_vision_schema.py`; reuse detail photo URLs already stored in `libs/common/models.py`.

- [ ] Define Pydantic `VisionExtraction`: item class (`exact_device`, `device_bundle`, `accessory`, `parts_broken`, `wrong_variant`, `uncertain`), nullable model and variant, included accessories, seller condition claims, visible defects, contradictions, unknown fields and evidence references. Evidence uses image index or quoted text span. Reject invalid classes and out-of-range image references.
- [ ] Provide `prepare_images(urls: list[str]) -> list[ImageInput]`, where `ImageInput` holds image bytes, MIME type and SHA-256 digest. Select up to three distinct photographs; prefer overview, label and included parts when metadata permits; otherwise use stable provider order. Preserve aspect ratio and start with a 768-pixel long edge; benchmark label crops separately.
- [ ] Restrict image hosts to provider/CDN configuration, enforce public IP destinations through redirects and connection resolution, reject private/metadata addresses, enforce timeouts and byte/pixel limits, decode and re-encode JPEG/PNG. Images and descriptions are untrusted data, never executable model instructions.
- [ ] Add fixtures for an accessory-only photo, empty image list, invalid JSON output, redirect to private IP, oversized/decompression-bomb image and failed fetch. Missing images produce an explicit text-only result, never an invented visual assessment.
- [ ] Run `uv run pytest tests/unit/test_listing_images.py tests/unit/test_vision_schema.py -q`; commit the schema and image preparation unit.

## Task 3: Add one adapter, cache and enforceable spending controls

**Files:** new `libs/common/vision_service.py`, `tests/unit/test_vision_service.py`; modify `libs/common/settings.py`, `libs/common/models.py`; create the next available Alembic revision at execution time.

- [ ] Expose `extract_listing(title: str, description: str, images: list[ImageInput]) -> VisionExtraction`. Implement Google and Scaleway adapters behind that interface; local llama.cpp can reuse the OpenAI-compatible request format. Disable tools and browsing in extraction requests.
- [ ] Use one active provider, bounded output, concurrency two for hosted calls and one for local. Retry only transient failures with bounded backoff; schema failure and timeout become explicit unknown/error statuses. Do not silently call a second paid model.
- [ ] Cache by exact model, prompt/schema version, normalized text and image content hashes. Use a unique request key and persistent in-flight claim so overlapping workers do not repeatedly charge for identical work. Changing model or photos invalidates the cache.
- [ ] Persist input/output/reasoning usage where supplied, provider currency, price version, duration, cache status and errors. Missing usage is unknown, not zero; hold conservative budget reservation until reconciled.
- [ ] Add atomic budget reservation before each call using conservative input and maximum output. Proposed initial limits: €5 evaluation and €10/month production on Scaleway; Google budget tracked separately in USD. These are proposed limits, not existing enforced settings. Exhaustion leaves candidates pending and visible. Account for failed attempts and retries; reconcile with provider billing.
- [ ] Test cache invalidation, duplicate concurrent requests, output validation, 429 recovery, missing usage and simultaneous calls at the budget limit. Run `uv run pytest tests/unit/test_vision_service.py -q` and PostgreSQL integration tests for reservations; commit independently.

## Task 4: Integrate without discarding ambiguous candidates too early

**Files:** `ingestion/filtering.py`, `ingestion/ingestion.py`, `ingestion/enrichment.py`, `ingestion/enrichment_prompt.py`, `ingestion/worker.py`; existing relevance tests and a new pipeline integration test.

- [ ] Route hard exclusions first, then fetch detail/photos for a bounded pool of plausible and uncertain candidates, then run factual extraction, then apply relevance and value rules. Avoid rejecting every ambiguous title before photos can resolve it.
- [ ] Reuse the same result for relevance and enrichment. Remove unsupported photo-quality/authenticity guesses from the text-only prompt. Keep bundle comparisons separate from bare-device prices; missing exact variant abstains where variant materially affects price.
- [ ] Verify an accessory whose title contains PS5 never reaches the alert path; a real device with accessories can remain a bundle candidate; a photo/text mismatch is reviewable; absent France delivery evidence still blocks promotion.
- [ ] Expose provider/model, extracted evidence, uncertainty and budget exhaustion in the existing detail/health UI. Preserve current Telegram functionality; do not send test messages as part of offline evaluation.
- [ ] Run `uv run pytest tests/unit/ -q` plus relevant PostgreSQL pipeline tests. Apply repository formatting using `uv run ruff check --fix .` and `uv run ruff format .`, inspect unrelated changes, then commit this unit.

## Task 5: Compare quality, latency and cost; activate the winner

**Files:** `scripts/evaluate_listing_vision.py`, new `docs/listing-vision-evaluation.md`, optional Compose profile for local inference.

- [ ] On development data, compare Gemini 2.5 Flash text-only versus the same model with actual photos first. Then hold prompt, input images and decoding fixed while comparing Flash-Lite, Scaleway Mistral Small and local SmolVLM2. Local runtime/artifact hashes are mandatory. Do not change prompt and model together and attribute the result solely to the model.
- [ ] Report per-field precision/recall, class confusion, accessory false acceptance, coverage/abstention, provider/product slices, bootstrap confidence intervals, input/output tokens, cost per thousand and p50/p95 end-to-end latency. Final held-out evaluation happens once after selection; small samples cannot certify scam safety.
- [ ] Benchmark local inference on a disposable available machine first; buying a VPS to discover latency is unnecessary. Before recommending migration, verify peak RSS and throughput on comparable OVH CPU capacity while the application runs. Estimated target: no OOM, app latency unaffected, queue drains within its configured ingestion interval. Actual latency remains unmeasured until this step.
- [ ] Proposed activation criteria: no regression in exact-device precision versus baseline; accessory false acceptance decreases with reported uncertainty; uncertain outputs cannot alert; image failures cannot invent evidence; spending reservations hold under concurrency. If the small holdout cannot resolve a difference, say so and collect more labeled data rather than claiming a winner.
- [ ] Shadow the first 100 production candidates without additional alerts, inspect disagreements, then enable the chosen provider through configuration. Given unused production, use one normal deployment and smoke test, not a prolonged staged migration. Keep a configuration rollback to the supported text-only baseline.

Estimated delivery: 3–5 engineering days including evaluation and integration, plus the stated labeling effort; optional OVH-specific CPU benchmark may add a day. No fine-tuning or dedicated GPU is needed to start. Expected outcome is less accessory noise and clearer evidence, not a measured accuracy or profit increase until evaluation and resale outcomes exist.

## Sources

- [Scaleway serverless and dedicated pricing](https://www.scaleway.com/en/pricing/model-as-a-service/)
- [Scaleway model support and lifecycle](https://www.scaleway.com/en/docs/generative-apis/reference-content/supported-models/)
- [Google Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Google model shutdown schedule](https://ai.google.dev/gemini-api/docs/deprecations)
- [OVH France advertised VPS configurations](https://www.ovhcloud.com/fr/vps/best-vps/)
- [llama.cpp multimodal runtime and supported quantizations](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
- [SmolVLM2 model card](https://huggingface.co/HuggingFaceTB/SmolVLM2-2.2B-Instruct)

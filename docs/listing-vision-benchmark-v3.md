# V3 benchmark: local evaluation complete; hosted holdout pending

Status: implementation complete; benchmark incomplete. Development results below are measured against frozen assistant-assessed references, not independent human ground truth. Selection and untouched holdout are required before choosing a production model. No production settings were changed.

The seven-field directive contract replaces evidence and unknown-field generation. Validation: 501 unit tests and 35 real PostgreSQL integration tests passed. Implementation commit: `abeead6`.

## Measured development results

Each complete row uses the same 30 listings, frozen photos, directive prompt, flat JSON Schema, temperature zero and 512 output tokens. Latency is observed request wall time, with local load/swap effects; it is not controlled warm throughput. Costs are derived from reported token counts and provider prices, not invoices. Local API charges exclude electricity and hardware.

| Model | Valid / attempted | Class matches / scored | Median seconds | API cost / 1,000 | Estimated 5,000/month | Estimated 20,000/month |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini-3.1-flash-lite | 30/30 | 24/30 | 1.98 | $1.044 | $5.22 | $20.87 |
| gemini-3.5-flash-lite | 30/30 | 23/30 | 2.09 | $1.339 | $6.70 | $26.78 |
| local-gemma3_4b | 30/30 | 18/30 | 10.39 | local 0.000 | local 0.00 | local 0.00 |
| local-qwen3-vl_4b-instruct | 30/30 | 18/30 | 13.00 | local 0.000 | local 0.00 | local 0.00 |
| local-qwen3-vl_8b-instruct | 30/30 | 18/30 | 14.92 | local 0.000 | local 0.00 | local 0.00 |
| local-smolvlm2-2.2b-q4_k_m | 30/30 | 14/30 | 4.64 | local 0.000 | local 0.00 | local 0.00 |
| scaleway-gemma-4-26b-a4b-it | 30/30 | 24/30 | 1.08 | €0.436 | €2.18 | €8.72 |
| scaleway-mistral-medium-3.5-128b | 30/30 | 26/30 | 2.58 | €4.415 | €22.08 | €88.30 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | 30/30 | 23/30 | 5.37 | €0.407 | €2.03 | €8.14 |
| scaleway-pixtral-12b-2409 | 30/30 | 20/30 | 4.60 | €1.167 | €5.83 | €23.33 |
| scaleway-qwen3.5-397b-a17b | 30/30 | 29/30 | 1.92 | €1.548 | €7.74 | €30.95 |
| scaleway-qwen3.6-35b-a3b | 30/30 | 27/30 | 1.34 | €0.621 | €3.11 | €12.43 |

The title-only heuristic matched 18/30 development classes. Comparisons use 24 independent listing groups; seven stock-image relistings share one group. The paired confidence intervals below reflect this dependence. These small development differences do not establish a reliable winner.

| Model | Class delta vs heuristic | Paired group 95% interval |
| --- | ---: | ---: |
| gemini-3.1-flash-lite | +20.0 pp | [-10.0, +58.3] pp |
| gemini-3.5-flash-lite | +16.7 pp | [-16.7, +58.3] pp |
| local-gemma3_4b | +0.0 pp | [-13.3, +13.3] pp |
| local-qwen3-vl_4b-instruct | +0.0 pp | [-13.3, +13.3] pp |
| local-qwen3-vl_8b-instruct | +0.0 pp | [-13.3, +13.3] pp |
| local-smolvlm2-2.2b-q4_k_m | -13.3 pp | [-36.1, +16.7] pp |
| scaleway-gemma-4-26b-a4b-it | +20.0 pp | [-14.3, +62.5] pp |
| scaleway-mistral-medium-3.5-128b | +26.7 pp | [+7.1, +53.4] pp |
| scaleway-mistral-small-3.2-24b-instruct-2506 | +16.7 pp | [+0.0, +37.5] pp |
| scaleway-pixtral-12b-2409 | +6.7 pp | [-9.5, +25.0] pp |
| scaleway-qwen3.5-397b-a17b | +36.7 pp | [+14.3, +66.7] pp |
| scaleway-qwen3.6-35b-a3b | +30.0 pp | [+10.0, +54.2] pp |

## Per-field surface agreement is not semantic correctness

Precision/recall below use frozen literal labels after case/whitespace normalization. Equivalent French/English phrases and model aliases can be penalized; these are conservative surface-agreement metrics, not a validated semantic score. Null reference fields are unscored. Lists are scored by matching items; scalar values are scored by equality. Unknown metrics are shown as —. The obsolete V2 visible_defects field is excluded.

| Model | Field | Precision | Recall | Scored rows |
| --- | --- | ---: | ---: | ---: |
| gemini-3.1-flash-lite | item_class | 80.0% | 80.0% | 30/30 |
| gemini-3.1-flash-lite | model | 88.5% | 88.5% | 26/30 |
| gemini-3.1-flash-lite | variant | 55.6% | 50.0% | 10/30 |
| gemini-3.1-flash-lite | included_accessories | 26.1% | 29.4% | 25/30 |
| gemini-3.1-flash-lite | seller_reported_faults | 50.0% | 50.0% | 30/30 |
| gemini-3.1-flash-lite | visible_damage | 0.0% | 0.0% | 24/30 |
| gemini-3.1-flash-lite | text_photo_conflict | 88.5% | 88.5% | 26/30 |
| gemini-3.5-flash-lite | item_class | 76.7% | 76.7% | 30/30 |
| gemini-3.5-flash-lite | model | 69.2% | 69.2% | 26/30 |
| gemini-3.5-flash-lite | variant | 60.0% | 60.0% | 10/30 |
| gemini-3.5-flash-lite | included_accessories | 24.1% | 26.5% | 25/30 |
| gemini-3.5-flash-lite | seller_reported_faults | 0.0% | 0.0% | 30/30 |
| gemini-3.5-flash-lite | visible_damage | 0.0% | 0.0% | 24/30 |
| gemini-3.5-flash-lite | text_photo_conflict | 92.3% | 92.3% | 26/30 |
| local-gemma3_4b | item_class | 60.0% | 60.0% | 30/30 |
| local-gemma3_4b | model | 34.6% | 34.6% | 26/30 |
| local-gemma3_4b | variant | 88.9% | 80.0% | 10/30 |
| local-gemma3_4b | included_accessories | 11.2% | 12.7% | 25/30 |
| local-gemma3_4b | seller_reported_faults | 0.0% | 0.0% | 30/30 |
| local-gemma3_4b | visible_damage | — | 0.0% | 24/30 |
| local-gemma3_4b | text_photo_conflict | 83.3% | 57.7% | 26/30 |
| local-qwen3-vl_4b-instruct | item_class | 60.0% | 60.0% | 30/30 |
| local-qwen3-vl_4b-instruct | model | 30.8% | 30.8% | 26/30 |
| local-qwen3-vl_4b-instruct | variant | 0.0% | 0.0% | 10/30 |
| local-qwen3-vl_4b-instruct | included_accessories | 21.1% | 23.5% | 25/30 |
| local-qwen3-vl_4b-instruct | seller_reported_faults | 0.0% | 0.0% | 30/30 |
| local-qwen3-vl_4b-instruct | visible_damage | — | 0.0% | 24/30 |
| local-qwen3-vl_4b-instruct | text_photo_conflict | 85.7% | 23.1% | 26/30 |
| local-qwen3-vl_8b-instruct | item_class | 60.0% | 60.0% | 30/30 |
| local-qwen3-vl_8b-instruct | model | 84.6% | 84.6% | 26/30 |
| local-qwen3-vl_8b-instruct | variant | 0.0% | 0.0% | 10/30 |
| local-qwen3-vl_8b-instruct | included_accessories | 23.9% | 26.5% | 25/30 |
| local-qwen3-vl_8b-instruct | seller_reported_faults | 50.0% | 50.0% | 30/30 |
| local-qwen3-vl_8b-instruct | visible_damage | — | 0.0% | 24/30 |
| local-qwen3-vl_8b-instruct | text_photo_conflict | 81.8% | 34.6% | 26/30 |
| local-smolvlm2-2.2b-q4_k_m | item_class | 46.7% | 46.7% | 30/30 |
| local-smolvlm2-2.2b-q4_k_m | model | 38.9% | 26.9% | 26/30 |
| local-smolvlm2-2.2b-q4_k_m | variant | 0.0% | 0.0% | 10/30 |
| local-smolvlm2-2.2b-q4_k_m | included_accessories | 12.6% | 10.8% | 25/30 |
| local-smolvlm2-2.2b-q4_k_m | seller_reported_faults | — | 0.0% | 30/30 |
| local-smolvlm2-2.2b-q4_k_m | visible_damage | — | 0.0% | 24/30 |
| local-smolvlm2-2.2b-q4_k_m | text_photo_conflict | 70.0% | 26.9% | 26/30 |
| scaleway-gemma-4-26b-a4b-it | item_class | 80.0% | 80.0% | 30/30 |
| scaleway-gemma-4-26b-a4b-it | model | 76.9% | 76.9% | 26/30 |
| scaleway-gemma-4-26b-a4b-it | variant | 0.0% | 0.0% | 10/30 |
| scaleway-gemma-4-26b-a4b-it | included_accessories | 27.2% | 30.4% | 25/30 |
| scaleway-gemma-4-26b-a4b-it | seller_reported_faults | 33.3% | 50.0% | 30/30 |
| scaleway-gemma-4-26b-a4b-it | visible_damage | — | 0.0% | 24/30 |
| scaleway-gemma-4-26b-a4b-it | text_photo_conflict | 88.0% | 84.6% | 26/30 |
| scaleway-mistral-medium-3.5-128b | item_class | 86.7% | 86.7% | 30/30 |
| scaleway-mistral-medium-3.5-128b | model | 80.8% | 80.8% | 26/30 |
| scaleway-mistral-medium-3.5-128b | variant | 77.8% | 70.0% | 10/30 |
| scaleway-mistral-medium-3.5-128b | included_accessories | 29.7% | 32.4% | 25/30 |
| scaleway-mistral-medium-3.5-128b | seller_reported_faults | 0.0% | 0.0% | 30/30 |
| scaleway-mistral-medium-3.5-128b | visible_damage | — | 0.0% | 24/30 |
| scaleway-mistral-medium-3.5-128b | text_photo_conflict | 76.9% | 76.9% | 26/30 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | item_class | 76.7% | 76.7% | 30/30 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | model | 7.7% | 7.7% | 26/30 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | variant | 57.1% | 40.0% | 10/30 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | included_accessories | 31.5% | 33.3% | 25/30 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | seller_reported_faults | 50.0% | 50.0% | 30/30 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | visible_damage | — | 0.0% | 24/30 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | text_photo_conflict | 81.8% | 69.2% | 26/30 |
| scaleway-pixtral-12b-2409 | item_class | 66.7% | 66.7% | 30/30 |
| scaleway-pixtral-12b-2409 | model | 73.1% | 73.1% | 26/30 |
| scaleway-pixtral-12b-2409 | variant | 60.0% | 60.0% | 10/30 |
| scaleway-pixtral-12b-2409 | included_accessories | 11.7% | 11.8% | 25/30 |
| scaleway-pixtral-12b-2409 | seller_reported_faults | 0.0% | 0.0% | 30/30 |
| scaleway-pixtral-12b-2409 | visible_damage | 0.0% | 0.0% | 24/30 |
| scaleway-pixtral-12b-2409 | text_photo_conflict | 76.9% | 76.9% | 26/30 |
| scaleway-qwen3.5-397b-a17b | item_class | 96.7% | 96.7% | 30/30 |
| scaleway-qwen3.5-397b-a17b | model | 65.4% | 65.4% | 26/30 |
| scaleway-qwen3.5-397b-a17b | variant | 30.0% | 30.0% | 10/30 |
| scaleway-qwen3.5-397b-a17b | included_accessories | 27.7% | 30.4% | 25/30 |
| scaleway-qwen3.5-397b-a17b | seller_reported_faults | 0.0% | 0.0% | 30/30 |
| scaleway-qwen3.5-397b-a17b | visible_damage | 33.3% | 33.3% | 24/30 |
| scaleway-qwen3.5-397b-a17b | text_photo_conflict | 84.6% | 84.6% | 26/30 |
| scaleway-qwen3.6-35b-a3b | item_class | 90.0% | 90.0% | 30/30 |
| scaleway-qwen3.6-35b-a3b | model | 92.3% | 92.3% | 26/30 |
| scaleway-qwen3.6-35b-a3b | variant | 50.0% | 50.0% | 10/30 |
| scaleway-qwen3.6-35b-a3b | included_accessories | 27.3% | 32.4% | 25/30 |
| scaleway-qwen3.6-35b-a3b | seller_reported_faults | 0.0% | 0.0% | 30/30 |
| scaleway-qwen3.6-35b-a3b | visible_damage | 0.0% | 0.0% | 24/30 |
| scaleway-qwen3.6-35b-a3b | text_photo_conflict | 84.6% | 84.6% | 26/30 |

## Dangerous class errors remain visible

Measured class predictions of exact_device/device_bundle on the negative cases below. These are class errors, not measured alerts passing the full application gates. Zero observed errors on one or two cases cannot establish safety.

| Model | Accessory accepted | Broken accepted | Wrong variant accepted |
| --- | ---: | ---: | ---: |
| gemini-3.1-flash-lite | 0/2 | 1/1 | 0/1 |
| gemini-3.5-flash-lite | 0/2 | 0/1 | 0/1 |
| local-gemma3_4b | 2/2 | 1/1 | 1/1 |
| local-qwen3-vl_4b-instruct | 0/2 | 1/1 | 1/1 |
| local-qwen3-vl_8b-instruct | 0/2 | 1/1 | 1/1 |
| local-smolvlm2-2.2b-q4_k_m | 1/2 | 1/1 | 1/1 |
| scaleway-gemma-4-26b-a4b-it | 0/2 | 0/1 | 0/1 |
| scaleway-mistral-medium-3.5-128b | 0/2 | 0/1 | 1/1 |
| scaleway-mistral-small-3.2-24b-instruct-2506 | 0/2 | 1/1 | 1/1 |
| scaleway-pixtral-12b-2409 | 0/2 | 1/1 | 1/1 |
| scaleway-qwen3.5-397b-a17b | 0/2 | 0/1 | 0/1 |
| scaleway-qwen3.6-35b-a3b | 0/2 | 1/1 | 0/1 |

## Limits that prevent an automatic production recommendation

- References were assessed before candidate outputs and then frozen. They contain identified ambiguities and one fault-label omission; they have not been changed to improve scores. The development split has only two accessories, one wrong variant and one broken device. The holdout contains no broken-device class examples, so broken-device recall cannot be established there.
- Frozen corpus: 90 listings, 30 development / 30 selection / 30 holdout; seed 42. Three old debug cases are development only. Exact/perceptual image checks and manual duplicate grouping were applied. The target negative-class quotas could not be met from the available real corpus.
- Benchmark target metadata includes explicit required attributes and normal accessories; the application pipeline currently supplies product name and search query. This is not an end-to-end production benchmark.
- Clean-photo appearance does not establish function, authenticity, scam safety or France delivery. Deterministic delivery, financial and review gates remain necessary. Class-level false acceptance is not equivalent to a production alert passing every gate.

## Volume and deployment decision

Estimated scenarios are 5,000 and 20,000 new analyses/month. Historical measurement was 478 new active listings over August 13–September 11; a partial September 12 day had 139. The base scenario uses 139 × 30 × 1.2 ≈ 5,000; expansion assumes four times that. These are not measured unique eligible photo-input cache misses. At the historical 478-listing monthly volume, multiply the per-1,000 rate by 0.478. Cost per correctly eligible production alert is not measured; it requires replaying the full gates on production-equivalent inputs and reviewed references. A 328-listing active backlog, retries and changed descriptions/photos add calls; multiply their counts by the measured per-attempt rate. Currency conversion is intentionally omitted.

Do not upgrade the OVH VPS based on this Mac benchmark: Apple Metal does not measure OVH CPU inference. Hosted costs are low enough at these volume assumptions that quality and operational reliability should decide. Keep Gemini 3.1 Flash-Lite as the existing provisional configuration until selection/holdout is complete; Scaleway Qwen 3.6 and Qwen 3.5 are candidates for that comparison. Pixtral was actually tested, but its announced October 1 retirement makes it a comparison candidate rather than a new deployment choice.

## Reproducibility and remaining work

Private inputs, references, photos, model artifacts, raw failures, usage and the atomic budget ledger remain under gitignored `data/vision-eval/v3/`. Manifest SHA-256: `edd3b65c5f50c41218f047bd3fac6360455d50d1888788a6eec808f7f50076bf`. Reference SHA-256: `858285d822fc54067260ba30b1631922a4ebb93a23d59403961111254575927c`. The reusable runner is `scripts/benchmark_listing_vision.py`; use `--help` and a single shared ledger across every run.

Remaining: complete hosted selection, freeze hosted finalists, run their untouched holdout once and finish controlled hosted timing comparisons. Local selection, its frozen holdout and repeated timing measurements are complete. Hosted selection/holdout is paused because automatic approval review requested explicit permission for the text/photo payloads and splits. Gemini selection had already completed under a separate approval; it is not mixed into this development table.

The community SmolVLM Ollama artifact lacked a vision projector and failed image inference; it is not counted as a valid VLM benchmark. The official SmolVLM model plus matching projector completed all 30 development cases and is included above. Local swap was already about 25 GiB when monitoring began; no clean pre-task baseline exists. Reported local latency must therefore be treated as performance under the current desktop workload.

See [development ablation](listing-vision-development-ablation-v3.md) and [error analysis](listing-vision-development-error-analysis-v3.md).

Pricing/lifecycle sources checked September 13, 2026: [Scaleway pricing](https://www.scaleway.com/en/pricing/model-as-a-service/), [Scaleway supported models](https://www.scaleway.com/en/docs/generative-apis/reference-content/supported-models/), [reasoning controls](https://www.scaleway.com/en/docs/generative-apis/how-to/query-reasoning-models/), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

## Local artifact identity and memory observations

Measured on Apple M5 with 24 GiB physical RAM. Native Ollama version 0.32.11 uses Metal and context 8,192; the official SmolVLM runner uses llama.cpp build 0b1bad14f. These are local workstation measurements, not OVH CPU benchmarks.

| Model | Artifact identity |
| --- | --- |
| gemma3:4b | `a2af6cc3eb7fa8be8504abaf9b04e88f17a119ec3f04a3addf55f92841195f5a` |
| qwen3-vl:4b-instruct | `ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b` |
| qwen3-vl:8b-instruct | `0533d74300e4f9bc367d675d4e64ffd073d50ff16a2b4096cc2e8a1cf8c96319` |
| smolvlm2-2.2b-q4_k_m | `1b2fe1e467957341233dec4a0604b4e6e4b6fdfcf8b992d21d66df5031ab83e6` |

The Smol artifact identity above fingerprints its artifact record; the publisher revision is `1bc3c9f74ceafd4c8d4411cc9cf188bba3798f91`, with Q4_K_M weights and the matching Q8_0 projector. Native local model sizes reported by Ollama are 4.4B (Qwen 4B), 4.3B (Gemma 3) and the Qwen 8B artifact.

Sampled maximum Ollama allocated model memory: Qwen 4B 3.88 GiB, Gemma 3 3.62 GiB, Qwen 8B 6.10 GiB. Server process RSS observed during these intervals reached 5.70, 8.00 and 8.86 GiB respectively; transition samples can include a loading model and are not isolated per-artifact peaks. The first swap sample was already 25,947.94 MiB and later samples reached 36,695.25 MiB; other applications remained running. There is no clean pre-task baseline, so swap changes cannot be attributed entirely to a model.

Estimated energy only: assuming an additional 20–40 W at €0.25/kWh, 72 compute-hours costs €0.36–€0.72. Keeping a machine drawing that additional power on for 720 hours costs €3.60–€7.20. Neither power consumption nor electricity tariff was measured; these exclude hardware, maintenance and idle behavior.

All development local prompt counts were below 3,806 tokens. One Smol selection request exceeded the fixed 8,192 context and failed; the error remains in the denominator. Native Ollama records context saturation risk, which must be reviewed before treating selection outputs as complete-input comparisons.

Official SmolVLM measurement: 259 one-second server RSS samples, sampled peak 8.09 GiB; cold server health-ready time 1.040–1.061 seconds. These are process RSS/startup measurements, not total Metal allocation or cold first-listing latency.

Detailed field and source/product slice metrics are saved in private `data/vision-eval/v3/detailed-metrics.json`, generated from the frozen inputs by `export_metrics.py`. This file contains aggregate metrics, while original photos/text and outputs remain separate.

The local control is selected using unsafe class-error count, then scorable class matches, then median latency. Literal free-text field scores are not suitable as a semantic ranking criterion here. The choice is frozen before local holdout inference in private `local-finalist.json`; it is not a production recommendation. Runtime repetitions use a seeded ten-case development subset and are excluded from prediction-quality sample counts.

Corpus provider coverage: 20 ebay, 70 leboncoin. No Vinted or Cash Converters examples are represented; these connector-specific input conditions are unmeasured.

## Completed selection results

Measured on the separate 30-listing selection split. One class reference is unscored, leaving 29 class assessments. No broken-device class examples exist in this split; the accessory/wrong-variant denominator is only three. These results were not used to tune the prompt.

| Model | Valid outputs | Class matches | Unsafe class errors / 3 | Median seconds |
| --- | ---: | ---: | ---: | ---: |
| gemini-3.1-flash-lite | 30/30 | 28/29 | 0/3 | 2.16 |
| gemini-3.5-flash-lite | 29/30 | 27/29 | 0/3 | 2.18 |
| local-gemma3_4b | 30/30 | 20/29 | 3/3 | 9.60 |
| local-qwen3-vl_4b-instruct | 30/30 | 23/29 | 1/3 | 10.78 |
| local-qwen3-vl_8b-instruct | 30/30 | 22/29 | 0/3 | 16.73 |
| local-smolvlm2-2.2b-q4_k_m | 29/30 | 8/29 | 2/3 | 4.70 |

Qwen 8B is frozen as the local holdout control: zero unsafe class errors versus one for Qwen 4B, despite Qwen 4B matching one more class and running faster. This is a tradeoff on a very small sample, not a statistically established safety advantage. The full native request/response configurations are unchanged from development.

Scaleway selection is not run: automatic approval review requested explicit permission to send the frozen selection/holdout text and photos to the hosted providers. Gemini selection had already completed under its separate approval. The remaining hosted matrix and final production recommendation are pending.

## Local holdout: bundles and photo coverage remain weak

Measured once after freezing Qwen 8B as the local control: 29/30 validated outputs, 22/29 scorable class matches, median observed request time 15.37 seconds across 29 timed inference calls. The oversized HTML listing was rejected before inference and remains an input failure. The title-only heuristic matched 17/29 classes; the paired group bootstrap delta is +17.24 percentage points, 95% interval [0.00, +34.48], seed 42 and 2,000 resamples. This small sample does not establish a clear statistical improvement.

The model correctly classified all 19 ordinary devices and both wrong variants. It mislabeled six of seven bundles as ordinary devices and the single accessory as broken parts. It did not accept any of the three accessory/wrong-variant cases, but no broken-device class examples exist in this holdout. Zero observed false acceptance is not proof of safety.

Among 29 validated outputs, visible_damage was null for 21 and text_photo_conflict was null for 19. These are unassessed fields, not evidence of clean or consistent photos. The production gate would require review for those unknowns. Class-level results do not measure the full alert pipeline or purchase profitability. Six missed bundles also prevent recommending this model as an automatic bundle filter.

The local result therefore supports retaining hosted inference as the provisional approach and avoiding a RAM purchase for this model. Hosted finalists still need their selection and holdout comparisons before a final model choice.

## Repeated inputs are faster because the runtime reuses prompt state

Measured Qwen 8B timing experiment: one untimed-for-summary warmup, then the same ten development inputs in three seeded randomized orders. All 31 fresh endpoint calls returned schema-valid outputs. They bypassed the application's result cache; repeats are not additional prediction-quality cases.

| Pass | Calls | Median request time | p95 nearest rank | Median prompt-evaluation time |
| --- | ---: | ---: | ---: | ---: |
| First ordered pass | 10 | 16.032 s | 17.293 s | 11.197 s |
| Second ordered pass | 10 | 3.538 s | 5.177 s | 0.050 s |
| Third ordered pass | 10 | 3.239 s | 4.266 s | 0.050 s |

One first-pass input had also served as warmup. The dramatic later-pass reduction is consistent with native prompt/KV-state reuse; it is not fresh-listing throughput. For the 29 distinct timed holdout calls, mean request time was 15.700 seconds, median 15.374 seconds, and measured sequential throughput was 3.82 validated outputs/minute. Request timing excludes model download and external application scheduling. System load and swap were not controlled.

Derived capacity, assuming the holdout's mean time transfers to production and ignoring retries: approximately 21.8 compute-hours for 5,000 calls, or 87.2 hours for 20,000. These are planning estimates, not OVH measurements. Retaining all cached states can affect memory; the application should normally avoid identical inference through its own durable result cache.

Final token-derived benchmark charges in the shared ledger: $0.1545174 and €0.2727708, versus authorized planning caps of $5 and €10. No monetary reservation remains outstanding. These are recorded usage charges, not reconciled invoices; prior historical benchmark budgets are separate. Local calls incur no API charge. The dedicated Ollama server and official SmolVLM server were stopped after measurement; model files and private results remain available for reproduction.

# Hosted Gemini remains the provisional choice at approximately 5,000 analyses/month

This is a measured integration/performance smoke comparison, with assistant-assessed references. It is not a statistically reliable quality benchmark. Production settings were not changed.

## Volume: budget for 5,000 calls, with a 20,000-call expansion scenario

Measured from read-only VPS PostgreSQL queries on September 13, 2026, shortly after midnight Europe/Paris:

- August 13–September 11 inclusive: 1,043 newly seen listings, of which 478 were active rather than sold. These are unique database listings, not fetch events.
- September 12 through the query time (still a partial UTC day): 156 newly seen listings, of which 139 were active. Ingestion logged 2,280 fetched and 633 persisted events; repeats must not be billed as unique analyses. Six products are active.
- Fresh active stock: 328 listings. This is a potential initial backlog, not recurring monthly volume.

Estimated working volume: 139 new active listings/day × 30 × 1.2 allowance for changed inputs and coverage = 5,004, rounded to **5,000 analyses/month**. This uses the new ingestion cadence, not the much slower historical average. One day does not establish a stable run rate. The photo-eligible fraction and changed-input cache-miss rate are not measured, so 5,000 is a budgeting assumption rather than a precise demand forecast. Exact cached inputs should not trigger another paid call.

Estimated expansion scenario: **20,000/month**, four times the base for recovered providers and more products. Vinted currently contributes errors/no-data, so future recovery can materially increase demand. This is a scenario, not a prediction. Measure daily unique eligible input hashes, cache hits and actual calls over the next seven full days to replace both assumptions.

## Inputs, reference assessment and reproducibility

All models receive the same three actual listings, each with three frozen JPEG photographs, target, title and full description. Temperature 0; output cap 1,200 tokens; factual-listing-v2 prompt and listing-vision-v2 schema. Image hashes are verified. Gemini uses minimal thinking; local uses seed 42, 8,192-token context and one request at a time. Provider-specific schema enforcement and image tokenization differ: this compares complete model/runtime options, not model weights in isolation.

Gemini results are reused from the September 12 VPS run on these exact inputs, avoiding payment for identical requests. Scaleway and local models were run September 13. Network origins differ, so hosted latency includes their respective client-to-provider paths. Local calls use Ollama 0.32.11 and Metal on an Apple M5 with 24 GiB RAM. Each local model runs the three cases twice; only the first request includes initial model loading, and repeats can benefit from runtime caches. A repeat is not another independent quality example.

I inspected the photographs and descriptions and froze reference-assessments.json before reading local/Scaleway predictions. These three cases had already appeared in Gemini protocol debugging, so the assessments are **not blinded to all prior Gemini output, not independent human ground truth, and not an untouched holdout**. No tuning was performed against this comparison.

| Case | Assessed class | Assessed identity/variant | Decision-relevant evidence |
| --- | --- | --- | --- |
| PS5 | device_bundle | PlayStation 5, disc edition | Console, controller, game and extra stand; seller explicitly says cash-only pickup. Reject for required delivery independently of VLM identity. |
| GoPro | device_bundle | Hero 11 Black | Camera, batteries and multiple accessories; 11 BLACK visible on camera. Some included items are only seller claims. |
| Sony headphones | exact_device | WH-1000XM4, black | Headphones with ordinary carrying case/cable; these standard accessories do not create a resale bundle. |

No obvious damage is established in the supplied photos. That is not evidence of working condition, authenticity or absence of hidden defects. No accessory-only, broken-device or scam-positive examples exist in this sample; recall for those categories is unmeasured. Accessory descriptions need semantic matching and separate seller-claim/visual verification; I have not converted guessed equivalences into precision scores.

The current title-only heuristic gets the PS5 and headphones class right and misses the GoPro bundle: **2/3 class agreement**. This is the baseline for these cases only. Gemini gets both bundles right. Scaleway calls both exact devices; it also describes the missing PS5 box as a visible defect, causing validation rejection. Gemini 3.1 omits the GoPro variant field despite identifying Hero 11 Black in its model field; the current strict gate can therefore withhold an otherwise useful result. Gemini 3.5 additionally lists an airplane adapter for the headphones, which I could not confidently verify from these photos; do not treat that claim as confirmed.

## Measured runtime results with the original strict-schema adapter

| Option | Validated responses | Measured latency | Measured model memory |
| --- | ---: | --- | --- |
| Gemini 3.1 Flash-Lite | 3/3 | 1.59–2.02 s | Hosted |
| Gemini 3.5 Flash-Lite | 3/3 | 1.99–3.07 s | Hosted |
| Scaleway Mistral Small 3.2 | 2/3 | 4.01–9.36 s | Hosted |
| Qwen3-VL 4B Instruct Q4_K_M | 0/6 (three cases twice) | First request 44.95 s; warm repeat 4.97–33.14 s | 4.17 GB loaded |
| Gemma 3 4B Q4_K_M | 0/6 (three cases twice) | First request 15.99 s; warm repeat 3.38–7.22 s | 3.89 GB loaded |

Memory is Ollama's loaded model allocation, **not peak system RAM**. Measured system swap during the run was 23,313 MiB; there was no pre-run swap baseline, so the benchmark cannot attribute that to inference. Other applications were not closed. Peak memory, energy use and clean-machine latency are unmeasured. The model download ran during part of the initial pass, another reason to separate warm repeat timings. The local server is loopback-only and models run sequentially.

Qwen decoded approximately 34–38 tokens/s; Gemma approximately 40–41 tokens/s. Qwen's long PS5 output hit the 1,200-token cap. Both models repeatedly omitted the evidence.field property under Ollama's nested-schema handling. These are **model-plus-runtime compatibility results**, not evidence that their visual encoders cannot identify the products. Plain-JSON mode is assessed separately below; no failed strict-schema result is silently counted as successful.

For quality, Gemini class agreement is 3/3 versus the heuristic's 2/3 on these selected development cases; Scaleway raw class agreement is 1/3, before validation. This sample is too small and previously exposed to claim a population improvement. Conditional on accepted responses, Gemini model identity matches 3/3; variant precision/recall after semantic normalization is 2/2 and 2/3 for 3.1, 3/3 and 3/3 for 3.5. Scaleway accepted model identity matches 2/2 but omits both variants (recall 0/2; precision undefined). End-to-end coverage must retain the rejected third Scaleway case. Per-class false-acceptance rates for accessories and defective products cannot be measured here because their positive denominator is zero.

## Plain JSON mode recovers one local response, but not reliable extraction

An explicit adapter ablation changed only Ollama's response format from the full nested JSON Schema to plain JSON; the identical prompt still contains the schema and strict post-validation remains enabled. One fresh pass of the same three cases per model, without prompt tuning:

| Local option | Measured validated responses | Measured latency range | Outcome |
| --- | ---: | --- | --- |
| Qwen3-VL 4B | 1/3 | 13.69–42.32 s | GoPro validates but is misclassified as exact_device; PS5 truncates; headphones contain quotes absent from listing text. |
| Gemma 3 4B | 0/3 | 11.54–21.43 s | Invalid evidence sources, including simultaneous image and text attribution. |

The nested-schema failure is partly adapter-sensitive, so the original zero-success result should not be described as intrinsic model inability. However, the alternative also fails to meet the current contract. No usable local throughput or production accuracy is established. The one valid Qwen output identifies Hero 11 but calls the variant Noir; colour alone does not distinguish the Hero 11 Black model variant. This benchmark used 18 local requests total, three Scaleway requests, and reused six prior Gemini requests. No local model was promoted to production.

Decision: keep Gemini 3.1 as the economical provisional default; consider 3.5 for a larger reference-assessed comparison because it populated all three variants here, for roughly $4/month more at base volume. Do not auto-promote either solely from three cases. Scaleway needs bundle/evidence corrections before replacing Gemini. Local work should first simplify or flatten the extraction contract and evaluate at least 30 diverse assistant-assessed listings, including accessories, broken products and delivery conflicts, followed by independent review before claiming quality gains. Preserve these development cases separately from new evaluation cases. Larger local models or an OVH upgrade are not the first intervention.

## Prices are derived from actual token usage, not provider invoices

Standard per-million input/output rates checked September 13: Gemini 3.1 Flash-Lite $0.25/$1.50; Gemini 3.5 Flash-Lite $0.30/$2.50; Scaleway Mistral Small 3.2 €0.15/€0.35. Currency conversion, VAT, application hosting, retries and separate legacy LLM calls are excluded. No batch discount assumed. Failed validated outputs still incur token costs.

| Hosted option | Derived / 1,000 attempts | Derived / 5,000 | Derived / 20,000 |
| --- | ---: | ---: | ---: |
| Gemini 3.1 Flash-Lite | $1.52 | $7.61 | $30.45 |
| Gemini 3.5 Flash-Lite | $2.34 | $11.72 | $46.87 |
| Scaleway Mistral Small 3.2 | €0.64 | €3.19 | €12.74 |

Scaleway's three requests cost a derived €0.0019115, including the rejected response. Gemini's reused six requests cost a derived $0.0115973. Provider image token counts differ; applying Gemini's token count to Scaleway would give a misleading estimate. Future listing/photo lengths may differ from these examples.

Use a **$15/month provisional Gemini budget** at base volume, or **$50 at the 20,000-call scenario**, then adjust against measured spend. These are proposed limits, not changes applied to the deployment. Local inference has zero API charges but nonzero electricity, operator time and machine availability costs. Illustrative electricity only: assuming 10–30 seconds/request, 20–40 W incremental power and €0.25/kWh gives 14–42 compute-hours and €0.07–€0.42 at 5,000 attempts; 56–167 hours and €0.28–€1.67 at 20,000. These power and tariff assumptions are not measurements, exclude always-on idle power and hardware depreciation, and do not price successful extraction when outputs fail validation. Mac availability and correcting failures dominate such a small energy bill. Buying RAM for the OVH CPU VPS would not reproduce the Mac's Metal acceleration; no VPS upgrade is justified by this comparison.

Sources: [Google pricing](https://ai.google.dev/gemini-api/docs/pricing), [Scaleway pricing](https://www.scaleway.com/en/pricing/model-as-a-service/), [Scaleway model support](https://www.scaleway.com/en/docs/generative-apis/reference-content/supported-models/), [Qwen local artifact](https://ollama.com/library/qwen3-vl:4b-instruct), [Gemma local artifact](https://ollama.com/library/gemma3:4b).

Private reproducibility artifacts remain gitignored under data/vision-eval/: smoke.jsonl, smoke-images/index.json and hashed photos, reference-assessments.json, monthly-volume.json, vps-comparison-v2.json, scaleway-comparison.json, local-comparison.json, local_benchmark.py and scaleway_benchmark.py. Model manifest digests and loaded memory are saved with local records. Do not commit source listing descriptions, photos or credentials.

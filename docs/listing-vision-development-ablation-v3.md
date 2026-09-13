# Flattening fixes the response format; directive instructions improve classification

Measured on three previously used development examples with Scaleway Mistral Small 3.2, the flat contract produced valid outputs for all three listings. Flat fields alone did not make the classifications useful: the old wording produced three abstentions. The directive prompt matched two of the three frozen class references.

| Arm | Valid outputs | Class matches including failures | Median observed latency | Token-derived cost for three calls |
| --- | ---: | ---: | ---: | ---: |
| A: V2 schema and old instructions | 0/3 | 0/3 | 4.04 s | €0.00129630 |
| B: V3 flat schema, minimally adapted old instructions | 3/3 | 0/3 | 2.61 s | €0.00090275 |
| C: V3 flat schema and directive instructions, photos | 3/3 | 2/3 | 4.62 s | €0.00108105 |
| D: Same directive approach, text only | 3/3 | 1/3 | 1.23 s | €0.00042900 |

A had one truncated response and two responses missing the required evidence `field` key. B produced `uncertain` for all three listings. C correctly classified the PS5 bundle and headphones but still missed the GoPro's extra-battery bundle. D retained the PS5 bundle and missed the GoPro bundle; it abstained on the headphones where images helped C classify the item.

All arms used the same model ID (`mistral-small-3.2-24b-instruct-2506`), temperature zero, 512 output-token ceiling, provider-native JSON Schema mode, title, description and target metadata. SHA checks confirmed identical text envelopes and source photographs. D intentionally omitted the photos. No response repair or further prompt tuning was performed.

A uses the V2 system/schema from commit `5baa9b6` inside the common current input envelope, rather than replaying the historical duplicated-schema user payload. B removes evidence/unknown-field instructions and necessarily renames seller-condition/defect concepts to the new fault/damage fields. Consequently the A-to-B comparison approximates contract simplification; it does not isolate every schema and wording change independently.

C reused the exact main development-run outputs and incurred no new calls. The nine fresh calls cost **€0.00262805**, derived from reported token usage and configured prices, within the shared benchmark ledger. C's timings were observed earlier; these small, non-randomized samples are not throughput estimates.

These are prior debug cases with frozen assistant-assessed references, not independent ground truth or holdout evidence. The result supports separating format validity from extraction quality. It does not establish a general accuracy improvement or a reliable estimate of the value of photos.

Private reproducibility artifacts: `data/vision-eval/v3/run_ablation.py`, `ablation-dev.jsonl`, and `ablation-dev-summary.json`. They retain the full arm definitions, raw outputs, usage, costs, paired input hashes and reused-record provenance. No selection or holdout calls were made for this experiment.

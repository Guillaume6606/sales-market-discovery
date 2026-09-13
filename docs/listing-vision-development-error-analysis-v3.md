# Small models still need factual checks after JSON validation

Measured review: 21 deliberately selected development model/listing pairs across five models. These are examples selected to explain failures, not estimates of error prevalence. No selection or holdout predictions were inspected. Frozen labels and prompts were not changed.

Of these examples, the assistant review identified 15 model errors, one visual abstention, three ambiguous reference cases, one reference omission and one literal-scoring limitation. References remain assistant-assessed, not independent ground truth.

The recurring model failures were:

- **Bundle rules:** six reviewed cases missed explicitly included games, extra batteries or racing-wheel accessories. Some models extracted the extras but still classified the listing as an ordinary device.
- **Identity and precedence:** models sometimes copied contradictory product names, treated replacement parts as a broken device, or extracted a broken joystick while keeping the ordinary-device classification. Larger Gemini models sometimes confused a listing's colour claim with the target's required attributes.
- **Photo use:** some responses missed title/photo mismatches or visible wear. One local response abstained on usable images; another model promoted a text-only fault into visible damage. Valid JSON alone cannot demonstrate useful photo interpretation.

Three scoring limitations matter. A description explicitly mentioned wear where the frozen reference had no seller faults. Some visible marks could be dirt rather than permanent scratches. French and English phrases describing the same screen fault fail literal string matching. These observations must remain disclosed sensitivity issues; changing labels or adding post-hoc fuzzy matching would contaminate the frozen comparison.

Future work after the frozen evaluation should focus on deterministic bundle/fault precedence, clearer separation of target requirements from listing claims, and a reference protocol for ambiguous identity and surface wear. The current benchmark should finish without prompt tuning. Positive fault, conflict and unresolved-identity checks should continue to block automatic clearance.

The private `data/vision-eval/v3/error-analysis-dev.json` records each example, its expected and actual fields, reasoning, file hashes and coverage snapshot. At that snapshot, both Gemini development files and Pixtral/Mistral Small files contained 30 rows; local Qwen contained 26. A newly completed 30-row Gemma file was inventoried but not included in the 21-case qualitative review. Other runs were still ongoing. No accessory precision claim is derived from subjective phrase matching.

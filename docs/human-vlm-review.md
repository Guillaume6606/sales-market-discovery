# Review the five leading hosted VLMs locally

Start from the repository root:

```bash
PYTHONPATH=. uv run streamlit run ui/vision_review.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

Open http://127.0.0.1:8502. This standalone Streamlit interface uses the private,
gitignored `data/vision-eval/v3/` benchmark files. No database, VPS connection,
model credentials or inference calls are required.

1. Pick a listing. Inspect its description and the exact cached benchmark photos.
2. Enter your assessment and save it before revealing model outputs. Use Unknown
   when the evidence is insufficient; None means no item or damage reported.
3. Compare anonymous A–E answers. Positions are shuffled deterministically per listing.
   Rate each field Correct, Incorrect, or Cannot assess; leave others Not rated.
   Judge semantic meaning, not spelling or identical wording. Record corrections in notes.
4. Save the comparison before changing listings or closing the page. Saved ratings
   reload automatically and can be edited. The initial reference remains preserved.
5. Reveal model names and results when ready. Export all saved reviews as JSON.

The five defaults are Scaleway Gemma 4, Gemini 3.1 Flash Lite, Scaleway Qwen 3.5,
Qwen 3.6 and Mistral Medium 3.5, selected from the leading selection-set results.
All have outputs on the same 30 selection listings. These are existing evaluation
examples, not a fresh untouched human test. Assistant reference labels stay hidden
and unchanged. Model-name exposure in the current session is recorded with reviews;
this is a bias-reduction aid, not an enforceable blinded study.

Human ratings live separately in `data/vision-eval/v3/human-review/reviews.json`.
Saving uses a file lock and atomic replacement. This is a single-reviewer workspace;
multiple people should use separate copies. Export JSON for a portable backup.

The summary reports per-field correct and incorrect counts, their denominator,
and unassessable counts. It does not automatically interpret reference text as
exact-match truth. Historical timing and derived list-price costs are shown
separately. An empty damage list does not establish working condition or authenticity.

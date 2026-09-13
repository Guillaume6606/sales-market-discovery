"""Run with: uv run streamlit run ui/vision_review.py --server.port 8502."""

import hashlib
import json
from pathlib import Path
from typing import Any

import streamlit as st

from ui.lib.vision_review import FIELDS, MODELS, blind_order, load_corpus, load_reviews, save_review

ROOT = Path(__file__).resolve().parents[1] / "data/vision-eval/v3"
REVIEWS = ROOT / "human-review/reviews.json"
LABELS = {
    "item_class": "Item category",
    "model": "Device model",
    "variant": "Edition / variant",
    "included_accessories": "Included accessories",
    "seller_reported_faults": "Faults stated by seller",
    "visible_damage": "Damage visible in photos",
    "text_photo_conflict": "Text / photo contradiction",
}
VERDICTS = ["Not rated", "Correct", "Incorrect", "Cannot assess"]
CLASSES = [
    "uncertain",
    "exact_device",
    "device_bundle",
    "accessory",
    "parts_broken",
    "wrong_variant",
]


def display_value(value: Any) -> str:
    if value is None:
        return "Unknown / unassessed"
    if isinstance(value, list):
        return " · ".join(value) if value else "None reported"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def reference_form(listing: dict[str, Any], exposed: bool) -> None:
    st.subheader("1. Your assessment")
    st.caption(
        "Save your own reading before seeing model answers. Leave uncertain details unknown."
    )
    with st.form(f"reference-{listing['id']}"):
        reference: dict[str, Any] = {}
        missing_details = False
        reference["item_class"] = st.selectbox("Item category", CLASSES)
        st.caption(
            "exact_device = target device; device_bundle = device + valuable extras such as games; "
            "accessory = accessory only; parts_broken = broken / parts; wrong_variant = wrong "
            "device or edition; uncertain = insufficient information. Ordinary cables or a case "
            "alone do not make a bundle."
        )
        for field in ("model", "variant"):
            reference[field] = st.text_input(LABELS[field]) or None
        for field in ("included_accessories", "seller_reported_faults", "visible_damage"):
            state = st.selectbox(
                LABELS[field], ["Unknown", "None", "Present"], key=f"{listing['id']}-{field}"
            )
            details = st.text_area(f"{LABELS[field]} — one item per line", height=70)
            items = [line.strip() for line in details.splitlines() if line.strip()]
            missing_details |= state == "Present" and not items
            reference[field] = None if state == "Unknown" else [] if state == "None" else items
        conflict = st.selectbox(LABELS["text_photo_conflict"], ["Unknown", "No", "Yes"])
        reference["text_photo_conflict"] = {"Unknown": None, "No": False, "Yes": True}[conflict]
        notes = st.text_area("Notes / uncertainty")
        if st.form_submit_button("Save assessment & reveal answers", type="primary"):
            if missing_details:
                st.error("Please describe present items or select None / Unknown.")
                return
            save_review(
                REVIEWS,
                str(listing["id"]),
                {
                    "reference": reference,
                    "notes": notes,
                    "input_hash": listing["input_hash"],
                    "split": "selection",
                    "model_names_seen_before_reference": exposed,
                },
            )
            st.rerun()


def summary(reviews: dict[str, Any], predictions: dict[str, dict[str, Any]]) -> None:
    st.subheader("Human judgments")
    st.caption(
        "Measured from your saved ratings only. Correct / (correct + incorrect) excludes "
        "Cannot assess and Not rated. This is a review of the selection set, not a fresh test. "
        "No automatic matching against the earlier assistant labels is used."
    )
    rows = []
    for model in MODELS:
        for field in FIELDS:
            values = [r.get("ratings", {}).get(model, {}).get(field) for r in reviews.values()]
            correct, incorrect = values.count("Correct"), values.count("Incorrect")
            rows.append(
                {
                    "Model": model,
                    "Field": LABELS[field],
                    "Correct": correct,
                    "Incorrect": incorrect,
                    "Cannot assess": values.count("Cannot assess"),
                    "Rated denominator": correct + incorrect,
                    "Correct %": round(100 * correct / (correct + incorrect), 1)
                    if correct + incorrect
                    else None,
                }
            )
    st.dataframe(rows, hide_index=True, use_container_width=True)
    timing = []
    for model, results in predictions.items():
        valid = [r for r in results.values() if r.get("extraction")]
        seconds = sorted(float(r.get("wall_seconds", r.get("wall_ms", 0) / 1000)) for r in valid)
        costs = [float(r.get("cost", r.get("cost_usd", 0))) for r in valid]
        n = len(seconds)
        timing.append(
            {
                "Model": model,
                "Calls": n,
                "Measured median seconds": (seconds[(n - 1) // 2] + seconds[n // 2]) / 2
                if n
                else None,
                "Derived cost / 1,000 similar listings": 1000 * sum(costs) / n if n else None,
                "Currency": "USD" if model.startswith("Google") else "EUR",
            }
        )
    st.dataframe(timing, hide_index=True, use_container_width=True)
    st.caption("Historical benchmark timings and list-price token costs; no new calls made.")


def render_review() -> None:
    st.title("Judge the listing. Then judge the models.")
    st.caption("Local human review · 5 models · frozen selection set · no API calls")
    try:
        listings, predictions = load_corpus(ROOT)
        reviews = load_reviews(REVIEWS)
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"Cannot load review data: {exc}")
        st.stop()
    st.sidebar.header("Your review")
    complete = sum(bool(r.get("ratings")) for r in reviews.values())
    st.sidebar.progress(
        complete / len(listings), text=f"{complete} / {len(listings)} comparisons saved"
    )
    st.sidebar.caption("Form changes are saved when you press Save. One local reviewer workspace.")
    reveal = st.sidebar.checkbox("Reveal model names & results", key="reveal_names")
    if reveal:
        st.session_state["names_seen"] = True
    st.sidebar.download_button(
        "Export all saved reviews",
        json.dumps(
            {
                "version": "human-review-v1",
                "split": "selection",
                "models": MODELS,
                "reviews": reviews,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "human-vlm-reviews.json",
        "application/json",
    )
    st.sidebar.caption(f"Saved to {REVIEWS}")
    if reveal:
        with st.expander("Results & cost comparison"):
            summary(reviews, predictions)
    ids = [str(row["id"]) for row in listings]
    selected = st.sidebar.selectbox(
        "Listing",
        ids,
        format_func=lambda x: (
            ("✓ " if reviews.get(x, {}).get("ratings") else "")
            + x
            + " · "
            + next(row["target"] for row in listings if str(row["id"]) == x)
        ),
    )
    listing = next(row for row in listings if str(row["id"]) == selected)
    saved = reviews.get(selected, {})
    st.subheader(listing["title"])
    st.write(
        f"Target: **{listing['target']}** · Source: **{listing['source']}** · Listing {selected}"
    )
    st.json(listing.get("target_metadata", {}), expanded=False)
    st.text_area(
        "Original description (read-only)",
        listing["description"],
        height=200,
        disabled=True,
        key=f"desc-{selected}",
    )
    photos = listing.get("image_hashes", [])
    columns = st.columns(max(1, len(photos)))
    for column, digest in zip(columns, photos, strict=False):
        path = ROOT / "images" / f"{digest}.jpg"
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            column.error("Benchmark photo missing or changed")
        else:
            column.image(str(path), use_container_width=True)
    st.caption("These are the cached benchmark photos. Click an image to enlarge it.")
    st.info(
        "Unknown means unassessed. None reported means no issue was reported—not proof of working condition or authenticity."
    )
    if not saved.get("reference"):
        reference_form(listing, bool(st.session_state.get("names_seen")))
        return
    with st.expander("Your saved assessment"):
        st.json(saved["reference"])
        st.write(saved.get("notes", ""))
    st.subheader("2. Compare the five answers")
    st.caption(
        "A–E assignments vary by listing. Rate meaning, not exact wording. All seven fields can be reviewed."
    )
    order = blind_order(selected, list(MODELS))
    with st.form(f"ratings-{selected}"):
        ratings = {}
        for index, (column, model) in enumerate(zip(st.columns(5), order, strict=True)):
            with column:
                st.markdown(f"### {'ABCDE'[index]}")
                if reveal:
                    st.caption(model)
                result = predictions[model][selected]
                extraction = result.get("extraction")
                if not extraction:
                    st.error(f"No valid output: {result.get('status', 'missing')}")
                ratings[model] = {}
                for field in FIELDS:
                    st.markdown(f"**{LABELS[field]}**")
                    st.text(
                        display_value(extraction.get(field)) if extraction else "No valid output"
                    )
                    previous = saved.get("ratings", {}).get(model, {}).get(field, "Not rated")
                    ratings[model][field] = st.selectbox(
                        f"Judge {LABELS[field]}",
                        VERDICTS,
                        index=VERDICTS.index(previous),
                        key=f"{selected}-{model}-{field}",
                        label_visibility="collapsed",
                    )
        notes = st.text_area("Corrections / comparison notes", saved.get("comparison_notes", ""))
        if st.form_submit_button("Save comparison", type="primary"):
            if all(v == "Not rated" for fields in ratings.values() for v in fields.values()):
                st.error("Rate at least one field before saving.")
            else:
                save_review(
                    REVIEWS,
                    selected,
                    {
                        "ratings": ratings,
                        "comparison_notes": notes,
                        "model_names_seen_during_rating": bool(st.session_state.get("names_seen")),
                        "model_order": order,
                    },
                )
                st.rerun()
    if saved.get("ratings"):
        st.success("Comparison saved. Select another listing in the sidebar to continue.")


def main() -> None:
    st.set_page_config(page_title="VLM human review", page_icon="🔎", layout="wide")
    st.navigation([st.Page(render_review, title="VLM human review")], position="hidden").run()


if __name__ == "__main__":
    main()

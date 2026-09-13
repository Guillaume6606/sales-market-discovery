"""Export private labeling inputs, run cached vision, and report measured results.

Run with ``uv run python -m scripts.evaluate_listing_vision --help``.
No predictions are ever treated as ground-truth labels.
"""

import argparse
import asyncio
import hashlib
import json
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from ingestion.listing_images import ImageInput, prepare_images
from ingestion.relevance import classify_listing_relevance
from libs.common.settings import settings


def read_manifest(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids: set[str] = set()
    groups: dict[str, str] = {}
    for row in rows:
        if row["id"] in ids:
            raise ValueError("Duplicate listing ID")
        ids.add(row["id"])
        if row.get("input_hash") and row["input_hash"] != input_fingerprint(row):
            raise ValueError("Frozen input hash changed")
        if row["split"] not in {"dev", "selection", "holdout"}:
            raise ValueError("Split must be dev, selection or holdout")
        fingerprints = [f"group:{row['group_id']}"]
        fingerprints += [f"image:{value}" for value in row.get("image_hashes", [])]
        fingerprints += [f"perceptual:{value}" for value in row.get("perceptual_image_groups", [])]
        if row.get("title") or row.get("description"):
            fingerprints.append(f"content:{input_fingerprint(row)}")
        fingerprints += [f"url:{value}" for value in row.get("photo_urls", [])]
        for fingerprint in fingerprints:
            if fingerprint in groups and groups[fingerprint] != row["split"]:
                raise ValueError("Duplicate group/image crosses development and holdout")
            groups[fingerprint] = row["split"]
    return rows


async def frozen_images(manifest: Path, rows: list[dict[str, Any]]) -> dict[str, list[ImageInput]]:
    directory = manifest.parent / (manifest.stem + "-images")
    directory.mkdir(parents=True, exist_ok=True)
    index_path = directory / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    result = {}
    splits: dict[str, str] = {}
    for row in rows:
        record_id = row["id"]
        urls = row.get("photo_urls", [])
        if record_id in index:
            entry = index[record_id]
            if entry["urls"] != urls:
                raise ValueError("Photo URLs changed after freezing evaluation inputs")
            images = []
            for digest in entry["hashes"]:
                if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("Invalid frozen image hash")
                data = (directory / (digest + ".jpg")).read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("Frozen image content changed")
                images.append(ImageInput(data, "image/jpeg", digest))
        else:
            images = await prepare_images(urls)
            for image in images:
                (directory / (image.digest + ".jpg")).write_bytes(image.data)
            index[record_id] = {"urls": urls, "hashes": [image.digest for image in images]}
        hashes = [image.digest for image in images]
        if "image_hashes" in row and row["image_hashes"] != hashes:
            raise ValueError("Manifest image hashes do not match actual frozen input")
        for digest in hashes:
            if digest in splits and splits[digest] != row["split"]:
                raise ValueError("Actual duplicate image crosses development and holdout")
            splits[digest] = row["split"]
        result[record_id] = images
    index_path.write_text(json.dumps(index, indent=2))
    return result


def canonical(value: Any) -> Any:
    """Normalize text layout only, never infer synonyms or device equivalence."""
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if isinstance(value, list):
        return frozenset(canonical(item) for item in value)
    return value


def input_fingerprint(row: dict[str, Any]) -> str:
    data = {
        key: row.get(key)
        for key in (
            "target",
            "target_metadata",
            "critical_attributes",
            "normal_accessories",
            "title",
            "description",
            "image_hashes",
        )
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _extraction(row: dict[str, Any]) -> dict[str, Any]:
    result = row["result"]
    return (result.get("extraction") or {}) if result.get("status") == "completed" else {}


def paired_group_bootstrap(
    rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]] | None = None,
    field: str = "item_class",
    seed: int = 42,
    resamples: int = 2000,
) -> dict[str, Any]:
    """Paired exact-field correctness delta, sampling whole listing groups.

    Failed predictions are incorrect, including for uncertain reference labels.
    Without baseline_rows, compare class predictions with the title heuristic.
    """
    baseline = {row["id"]: row for row in baseline_rows} if baseline_rows is not None else None
    groups: dict[str, list[float]] = {}
    for index, row in enumerate(rows):
        truth = (row.get("expected") or {}).get(field)
        if truth is None:
            continue
        if baseline is None:
            if field != "item_class":
                raise ValueError("Heuristic baseline supports only item_class")
            guess = row.get("heuristic_class")
        else:
            other = baseline.get(row["id"])
            if other is None or input_fingerprint(row) != input_fingerprint(other):
                raise ValueError("Paired benchmark requires identical IDs and inputs")
            if canonical((other.get("expected") or {}).get(field)) != canonical(truth):
                raise ValueError("Paired benchmark requires identical references")
            guess = _extraction(other).get(field)
        actual = _extraction(row).get(field)
        delta = float(actual is not None and canonical(actual) == canonical(truth)) - float(
            guess is not None and canonical(guess) == canonical(truth)
        )
        groups.setdefault(str(row.get("group_id", row.get("id", index))), []).append(delta)
    values = list(groups.values())
    if not values:
        return {
            "delta": None,
            "ci95": None,
            "groups": 0,
            "rows": 0,
            "seed": seed,
            "resamples": resamples,
        }
    sums = np.array([sum(group) for group in values])
    sizes = np.array([len(group) for group in values])
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(values), size=(resamples, len(values)))
    deltas = sums[draws].sum(axis=1) / sizes[draws].sum(axis=1)
    return {
        "delta": float(sums.sum() / sizes.sum()),
        "ci95": [float(value) for value in np.quantile(deltas, [0.025, 0.975])],
        "groups": len(values),
        "rows": int(sizes.sum()),
        "seed": seed,
        "resamples": resamples,
    }


def _interval(outcomes: list[bool]) -> list[float] | None:
    if not outcomes:
        return None
    rng = np.random.default_rng(42)
    draws = np.sort(
        rng.binomial(len(outcomes), sum(outcomes) / len(outcomes), size=1000) / len(outcomes)
    )
    return [float(draws[24]), float(draws[974])]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = [row for row in rows if row.get("expected")]
    confusion: Counter[str] = Counter()
    field_results = {}
    for field in (
        "item_class",
        "model",
        "variant",
        "included_accessories",
        "visible_defects",
        "seller_reported_faults",
        "visible_damage",
        "text_photo_conflict",
    ):
        tp = predicted = expected = scored = matched = validated = 0
        for row in labeled:
            truth = row["expected"]
            if truth.get(field) is None:
                continue
            actual = _extraction(row).get(field)
            scored += 1
            validated += bool(_extraction(row))
            matched += actual is not None and canonical(actual) == canonical(truth[field])
            if field in {
                "included_accessories",
                "visible_defects",
                "seller_reported_faults",
                "visible_damage",
            }:
                gold, guess = canonical(truth[field]), canonical(actual or [])
                tp += len(gold & guess)
                predicted += len(guess)
                expected += len(gold)
            else:
                predicted += actual is not None
                expected += truth[field] is not None
                tp += actual is not None and canonical(actual) == canonical(truth[field])
        field_results[field] = {
            "precision": tp / predicted if predicted else None,
            "recall": tp / expected if expected else None,
            "true_positive": tp,
            "predicted": predicted,
            "expected": expected,
            "scored_rows": scored,
            "unscored_rows": len(rows) - scored,
            "exact_match": matched / scored if scored else None,
            "conditional_exact_match": matched / validated if validated else None,
            "validated_scored_rows": validated,
        }
    accepted = {"exact_device", "device_bundle"}
    accessory_outcomes = []
    unsafe = {key: [] for key in ("accessory", "parts_broken", "wrong_variant")}
    exact_outcomes = []
    baseline_correct = 0
    correct = 0
    class_rows = [row for row in labeled if row["expected"].get("item_class") is not None]
    for row in class_rows:
        gold = row["expected"]["item_class"]
        guess = _extraction(row).get("item_class", "failed")
        confusion[f"{gold} -> {guess}"] += 1
        correct += guess == gold
        baseline_correct += row.get("heuristic_class") == gold
        if gold in unsafe:
            unsafe[gold].append(guess in accepted)
        if gold == "accessory":
            accessory_outcomes.append(guess in accepted)
        if guess == "exact_device":
            exact_outcomes.append(gold == "exact_device")
    latencies = [row["wall_ms"] for row in rows if "wall_ms" in row]
    costs: dict[str, Decimal] = {}
    unknown_costs = 0
    for row in rows:
        usage = row["result"].get("usage") or {}
        if row["result"].get("cache_hit"):
            continue
        for attempt in usage.get("attempts", [usage]):
            if attempt.get("cost") is None:
                unknown_costs += 1
            else:
                currency = attempt["currency"]
                costs[currency] = costs.get(currency, Decimal(0)) + Decimal(attempt["cost"])
    return {
        "rows": len(rows),
        "labeled_rows": len(labeled),
        "class_labeled_rows": len(class_rows),
        "quality_status": "measured_on_supplied_labels"
        if labeled
        else "not_measured_no_human_labels",
        "reference_assessors": dict(
            Counter(str(row.get("assessor", "unspecified")) for row in labeled)
        ),
        "fields": field_results,
        "class_accuracy_paired_bootstrap": paired_group_bootstrap(rows),
        "unsafe_false_acceptance": {
            key: {
                "rate": sum(values) / len(values) if values else None,
                "denominator": len(values),
                "accepted": sum(values),
            }
            for key, values in unsafe.items()
        },
        "validated_response_rate": sum(bool(_extraction(row)) for row in rows) / len(rows)
        if rows
        else None,
        "confusion": dict(confusion),
        "class_accuracy": correct / len(class_rows) if class_rows else None,
        "heuristic_class_accuracy": baseline_correct / len(class_rows) if class_rows else None,
        "class_accuracy_delta": (correct - baseline_correct) / len(class_rows)
        if class_rows
        else None,
        "accessory_false_acceptance": sum(accessory_outcomes) / len(accessory_outcomes)
        if accessory_outcomes
        else None,
        "accessory_denominator": len(accessory_outcomes),
        "accessory_false_acceptance_bootstrap_95": _interval(accessory_outcomes),
        "exact_device_precision_bootstrap_95": _interval(exact_outcomes),
        "bootstrap_note": "Small or homogeneous samples can yield degenerate intervals; these do not establish safety.",
        "coverage": sum(
            bool(_extraction(row)) and _extraction(row).get("item_class") != "uncertain"
            for row in rows
        )
        / len(rows)
        if rows
        else None,
        "status_counts": dict(Counter(row["result"]["status"] for row in rows)),
        "cache_hits": sum(bool(row["result"].get("cache_hit")) for row in rows),
        "wall_ms_p50": median(latencies) if latencies else None,
        "wall_ms_p95": sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        if latencies
        else None,
        "known_cost_by_currency": {key: str(value) for key, value in costs.items()},
        "unknown_cost_calls": unknown_costs,
    }


async def run(args: argparse.Namespace) -> None:
    import time

    from libs.common.vision_service import extract_listing

    manifest = read_manifest(args.manifest)
    images = await frozen_images(args.manifest, manifest) if not args.text_only else {}
    selected = [row for row in manifest if row["split"] == args.split][: args.limit]
    if args.split == "holdout" and any(not row.get("expected") for row in selected):
        raise ValueError("Held-out evaluation requires frozen reference labels before running")
    settings.vision_enabled = True
    settings.vision_monthly_budget_eur = min(settings.vision_monthly_budget_eur, args.budget)
    settings.vision_monthly_budget_usd = min(settings.vision_monthly_budget_usd, args.budget)
    results = []
    for row in selected:
        started = time.monotonic()
        result = await extract_listing(
            row["title"],
            row.get("description") or "",
            [] if args.text_only else row.get("photo_urls", []),
            target=row["target"],
            prepared_images=[] if args.text_only else images[row["id"]],
        )
        results.append(
            {
                **row,
                "result": result.model_dump(mode="json"),
                "wall_ms": round((time.monotonic() - started) * 1000),
                "heuristic_class": classify_listing_relevance(
                    row["target"], row["target"], row["title"]
                ).classification.value,
            }
        )
    report = summarize(results)
    report["slices"] = {
        key: {
            value: summarize([row for row in results if row.get(key) == value])
            for value in sorted({row.get(key, "unknown") for row in results})
        }
        for key in ("source", "target")
    }
    report.update(
        model=settings.vision_model,
        provider=settings.vision_provider,
        split=args.split,
        seed=42,
        text_only=args.text_only,
        collected_at=datetime.now(UTC).isoformat(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"summary": report, "results": results}, indent=2, ensure_ascii=False)
    )
    print(json.dumps(report, indent=2))


def export(args: argparse.Namespace) -> None:
    from libs.common.db import SessionLocal
    from libs.common.models import ListingDetailORM, ListingObservation, ProductTemplate

    with SessionLocal() as db:
        rows = (
            db.query(ListingObservation, ListingDetailORM, ProductTemplate)
            .join(ListingDetailORM, ListingDetailORM.obs_id == ListingObservation.obs_id)
            .join(ProductTemplate, ProductTemplate.product_id == ListingObservation.product_id)
            .filter(ListingObservation.is_sold.is_(False))
            .order_by(ListingObservation.obs_id.desc())
            .limit(args.limit)
            .all()
        )
        records = []
        for obs, detail, product in rows:
            group = hashlib.sha256(
                json.dumps([obs.title, sorted(detail.photo_urls or [])]).encode()
            ).hexdigest()
            records.append(
                dict(
                    id=f"{obs.source}:{obs.listing_id}:{obs.product_id}",
                    group_id=group,
                    split="dev",
                    source=obs.source,
                    target=product.name,
                    title=obs.title,
                    description=detail.description,
                    photo_urls=detail.photo_urls or [],
                    expected=None,
                    collected_at=datetime.now(UTC).isoformat(),
                )
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n")
    print(
        f"Exported {len(records)} private, unlabeled development records. Group duplicates before splitting."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    exporter = commands.add_parser("export")
    exporter.add_argument("--output", type=Path, required=True)
    exporter.add_argument("--limit", type=int, default=250)
    runner = commands.add_parser("run")
    runner.add_argument("--manifest", type=Path, required=True)
    runner.add_argument("--output", type=Path, required=True)
    runner.add_argument("--split", choices=["dev", "selection", "holdout"], default="dev")
    runner.add_argument("--text-only", action="store_true")
    runner.add_argument("--limit", type=int, default=100)
    runner.add_argument("--budget", type=Decimal, default=Decimal("5"))
    args = parser.parse_args()
    if args.limit < 1 or (
        args.command == "run" and (not args.budget.is_finite() or not 0 <= args.budget <= 5)
    ):
        parser.error("Positive limit and finite evaluation budget between 0 and 5 required")
    if args.command == "export":
        export(args)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()

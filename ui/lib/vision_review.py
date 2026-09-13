"""Local human review data, kept separate from frozen benchmark references."""

import fcntl
import hashlib
import json
import os
import random
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIELDS = (
    "item_class",
    "model",
    "variant",
    "included_accessories",
    "seller_reported_faults",
    "visible_damage",
    "text_photo_conflict",
)
MODELS = {
    "Scaleway · Gemma 4": "scaleway-gemma-4-26b-a4b-it-selection-public.jsonl",
    "Google · Gemini 3.1 Flash Lite": "gemini-3.1-flash-lite-selection.jsonl",
    "Scaleway · Qwen 3.5": "scaleway-qwen3.5-397b-a17b-selection-public.jsonl",
    "Scaleway · Qwen 3.6": "scaleway-qwen3.6-35b-a3b-selection-public.jsonl",
    "Scaleway · Mistral Medium 3.5": "scaleway-mistral-medium-3.5-128b-selection-public.jsonl",
}


def blind_order(listing_id: str, models: list[str]) -> list[str]:
    """Stable per-listing shuffle avoids assigning a model a fixed screen position."""
    result = list(models)
    seed = int(hashlib.sha256(f"human-v1:{listing_id}".encode()).hexdigest(), 16)
    random.Random(seed).shuffle(result)  # noqa: S311
    return result


def load_reviews(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_review(path: Path, listing_id: str, patch: dict[str, Any]) -> None:
    """Merge under a lock and atomically replace to survive interrupted saves."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = load_reviews(path)
        records[listing_id] = {
            **records.get(listing_id, {}),
            **patch,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".review-")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(records, output, indent=2, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def load_corpus(root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    listings = [
        json.loads(line)
        for line in (root / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    predictions = {}
    for name, filename in MODELS.items():
        predictions[name] = {
            str(row["id"]): row
            for line in (root / filename).read_text().splitlines()
            if line.strip()
            for row in [json.loads(line)]
        }
    selected = [row for row in listings if row["split"] == "selection"]
    for row in selected:
        for name, outputs in predictions.items():
            prediction = outputs.get(str(row["id"]))
            if prediction is None or prediction["input_hash"] != row["input_hash"]:
                raise ValueError(f"Missing or mismatched benchmark input: {name}, {row['id']}")
    return selected, predictions

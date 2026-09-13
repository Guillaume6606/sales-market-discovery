"""Replay frozen listing benchmarks with persistent reservations and no automatic retries.

Private outputs include listing-derived model text. Keep output and ledger gitignored.
Run as ``uv run python -m scripts.benchmark_listing_vision --help``.
"""

import argparse
import asyncio
import base64
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from ingestion.listing_images import ImageInput
from libs.common import vision_service as vision
from libs.common.vision_schema import VisionExtraction
from scripts.evaluate_listing_vision import read_manifest


class FileBudget:
    """Lock a separate inode so replacing the ledger remains atomic across processes."""

    def __init__(self, path: Path, limits: dict[str, Decimal]):
        if any(not cap.is_finite() or cap < 0 for cap in limits.values()):
            raise ValueError("Budgets must be finite and nonnegative")
        self.path = path
        self.limits = limits
        path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {"attempts": []}
            yield state
            temporary = self.path.with_name(self.path.name + ".tmp")
            with temporary.open("w") as output:
                json.dump(state, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)

    def claim(self, key: str, currency: str, reservation: Decimal, retry_errors: bool = False):
        if not reservation.is_finite() or reservation < 0:
            raise ValueError("Reservation must be finite and nonnegative")
        with self.transaction() as state:
            previous = [a for a in state["attempts"] if a["key"] == key]
            if previous:
                last = previous[-1]
                if last.get("record") is None:
                    return None, {"status": "pending", "error": "Unresolved previous attempt"}
                if last["record"]["status"] == "completed" or not retry_errors:
                    return None, {**last["record"], "cache_hit": True}
            spent = sum(
                (Decimal(a["charged"]) for a in state["attempts"] if a["currency"] == currency),
                Decimal(0),
            )
            if currency not in self.limits or spent + reservation > self.limits[currency]:
                return None, {"status": "budget_exhausted"}
            attempt = str(uuid4())
            state["attempts"].append(
                {
                    "id": attempt,
                    "key": key,
                    "currency": currency,
                    "charged": str(reservation),
                    "reserved": str(reservation),
                    "record": None,
                }
            )
            return attempt, None

    def finish(self, attempt: str, record: dict[str, Any], cost: Decimal | None):
        with self.transaction() as state:
            entry = next(a for a in state["attempts"] if a["id"] == attempt)
            if entry["record"] is not None:
                raise ValueError("Attempt already finalized")
            if cost is not None:
                if not cost.is_finite() or cost < 0:
                    raise ValueError("Invalid actual cost")
                entry["charged"] = str(cost)
            entry["record"] = record


@contextmanager
def configured(args):
    overrides = {
        "vision_provider": args.provider,
        "vision_model": args.model,
        "vision_response_mode": args.mode,
        "vision_max_output_tokens": args.max_tokens,
        "vision_local_model_revision": args.local_digest,
    }
    previous = {key: getattr(vision.settings, key) for key in overrides}
    try:
        for key, value in overrides.items():
            setattr(vision.settings, key, value)
        yield
    finally:
        for key, value in previous.items():
            setattr(vision.settings, key, value)


async def ollama(client, base_url, path, payload=None):
    response = await (
        client.get(base_url + "/api/" + path)
        if payload is None
        else client.post(base_url + "/api/" + path, json=payload)
    )
    response.raise_for_status()
    return response.json()


def load_images(directory: Path, index: dict, row: dict) -> list[ImageInput]:
    hashes = index[row["id"]]["hashes"]
    if hashes != row["image_hashes"] or len(hashes) > 3:
        raise ValueError("Frozen image list differs from manifest")
    images = []
    for digest in hashes:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid image digest")
        data = (directory / (digest + ".jpg")).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Frozen image content changed")
        images.append(ImageInput(data, "image/jpeg", digest))
    return images


async def run(args):
    rows = [row for row in read_manifest(args.manifest) if row["split"] == args.split][: args.limit]
    if args.split in {"selection", "holdout"} and any(not row.get("expected") for row in rows):
        raise ValueError("Selection and holdout require frozen reference labels")
    index = json.loads((args.images / "index.json").read_text())
    with configured(args):
        currency, input_price, output_price = vision._price()
        reservation = (Decimal(65536) * input_price + args.max_tokens * output_price) / 1_000_000
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "rows": len(rows),
                        "currency": currency,
                        "maximum_reservation": str(reservation * len(rows)),
                        "paid_calls": 0,
                    }
                )
            )
            return
        ledger = FileBudget(
            args.ledger, {"USD": args.usd_cap, "EUR": args.eur_cap, "LOCAL": Decimal(0)}
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
            runtime = None
            if args.provider == "local":
                tags = await ollama(client, args.local_url, "tags")
                model = next((m for m in tags["models"] if m["name"] == args.model), None)
                if model is None or model["digest"] != args.local_digest:
                    raise ValueError("Local artifact missing or digest mismatch")
                runtime = await ollama(client, args.local_url, "version")
            for row in rows:
                images = load_images(args.images, index, row)
                if args.text_only:
                    images = []
                target = row.get("target_metadata", row["target"])
                prompt = vision.build_prompt(row["title"], row.get("description") or "", target)
                key = hashlib.sha256(
                    json.dumps(
                        [
                            vision.request_key(
                                row["title"], row.get("description") or "", images, target
                            ),
                            "native-ollama" if runtime else "hosted",
                            runtime,
                            args.local_url if runtime else None,
                            {"seed": 42, "num_ctx": 8192} if runtime else None,
                        ],
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                if len(prompt.encode()) > 32000:
                    attempt, cached = (
                        None,
                        {
                            "status": "input_error",
                            "error": "listing_text_limit",
                            "provider": args.provider,
                            "model": args.model,
                            "cost": "0",
                            "currency": currency,
                            "input_text_bytes": len(prompt.encode()),
                            "paid_attempt": False,
                            "cache_hit": False,
                        },
                    )
                else:
                    attempt, cached = ledger.claim(key, currency, reservation, args.retry_errors)
                if cached is not None:
                    record = {
                        **cached,
                        "id": row["id"],
                        "group_id": row["group_id"],
                        "split": row["split"],
                        "input_hash": row.get("input_hash"),
                        "request_key": key,
                    }
                else:
                    record = await run_case(
                        client,
                        args,
                        row,
                        images,
                        prompt,
                        key,
                        currency,
                        input_price,
                        output_price,
                        runtime,
                    )
                    cost = Decimal(record["cost"]) if record["cost"] is not None else None
                    ledger.finish(attempt, record, cost)
                with args.output.open("a") as output:
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(
                    json.dumps(
                        {
                            "id": row["id"],
                            "status": record["status"],
                            "cache_hit": record.get("cache_hit", False),
                        }
                    ),
                    flush=True,
                )


async def run_case(
    client, args, row, images, prompt, key, currency, input_price, output_price, runtime
):
    record = {
        "id": row["id"],
        "group_id": row["group_id"],
        "split": row["split"],
        "request_key": key,
        "provider": args.provider,
        "model": args.model,
        "artifact": args.local_digest,
        "runtime": runtime,
        "mode": args.mode,
        "prompt_version": vision.PROMPT_VERSION,
        "schema_version": vision.SCHEMA_VERSION,
        "price_version": vision.PRICE_VERSION,
        "max_output_tokens": args.max_tokens,
        "local_context_tokens": 8192 if args.provider == "local" else None,
        "input_text_bytes": len(prompt.encode()),
        "input_hash": row.get("input_hash"),
        "image_count": len(images),
        "system_hash": hashlib.sha256(vision.SYSTEM.encode()).hexdigest(),
        "currency": currency,
        "cost": None,
        "cache_hit": False,
    }
    started = time.perf_counter()
    try:
        if args.provider == "local":
            data = await ollama(
                client,
                args.local_url,
                "chat",
                {
                    "model": args.model,
                    "messages": [
                        {"role": "system", "content": vision.SYSTEM},
                        {
                            "role": "user",
                            "content": prompt,
                            "images": [base64.b64encode(i.data).decode() for i in images],
                        },
                    ],
                    "stream": False,
                    "format": VisionExtraction.model_json_schema()
                    if args.mode == "json_schema"
                    else "json",
                    "options": {
                        "temperature": 0,
                        "seed": 42,
                        "num_predict": args.max_tokens,
                        "num_ctx": 8192,
                    },
                    "keep_alive": "10m",
                },
            )
            raw = data["message"]["content"]
            usage = {
                "input_tokens": data.get("prompt_eval_count"),
                "output_tokens": data.get("eval_count"),
                "finish_reason": data.get("done_reason"),
            }
            record["runtime_metrics"] = {
                k: v for k, v in data.items() if k.endswith(("_duration", "_count"))
            }
            record["context_limit_risk"] = (
                usage["input_tokens"] >= 8192 - args.max_tokens
                if usage["input_tokens"] is not None
                else None
            )
            record["cost"] = "0"
        else:
            raw, usage = await vision._call_provider(prompt, images)
            cost = vision._cost(usage, input_price, output_price)
            record["cost"] = str(cost) if cost is not None else None
        record.update(raw=raw, usage=usage)
        if str(usage.get("finish_reason", "")).lower() in {
            "length",
            "max_tokens",
            "finishreason.max_tokens",
        }:
            raise ValueError("output_truncated")
        record["extraction"] = VisionExtraction.model_validate_json(
            raw, context={"image_count": len(images)}
        ).model_dump(mode="json")
        record["status"] = "completed"
    except Exception as exc:
        # Exception text can include credentials, request bodies or signed URLs.
        record.update(status="error", error=type(exc).__name__)
    record["wall_ms"] = round((time.perf_counter() - started) * 1000)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "images", "output", "ledger"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--provider", choices=["gemini", "scaleway", "local"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--split", choices=["dev", "selection", "holdout"], required=True)
    parser.add_argument("--mode", choices=["json_schema", "json_object"], default="json_schema")
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--usd-cap", type=Decimal, default=Decimal(5))
    parser.add_argument("--eur-cap", type=Decimal, default=Decimal(10))
    parser.add_argument("--local-url", default="http://127.0.0.1:11435")
    parser.add_argument("--local-digest", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    args = parser.parse_args()
    if args.limit < 1 or not 1 <= args.max_tokens <= 768:
        parser.error("Positive limit and max-tokens between 1 and 768 required")
    if any(not cap.is_finite() or cap < 0 for cap in (args.usd_cap, args.eur_cap)):
        parser.error("Budgets must be finite and nonnegative")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

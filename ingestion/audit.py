"""Connector data quality audit — LLM-as-judge for extraction verification."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from libs.common.models import ConnectorAudit, ListingObservation
from libs.common.settings import settings

VALID_VERDICTS = {"correct", "incorrect", "unverifiable"}

AUDITED_FIELDS = [
    "price",
    "title",
    "condition",
    "is_sold",
    "location",
    "seller_rating",
    "shipping_cost",
]

ANTIBOT_PATTERNS = re.compile(
    r"captcha|verify you are human|are you a robot|"
    r"connectez-vous pour continuer|veuillez vous connecter|"
    r"access denied|blocked|cloudflare|challenge-platform",
    re.IGNORECASE,
)


@dataclass
class AuditCapture:
    screenshot_path: str | None
    html_snippet: str | None


def parse_llm_verdict(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract per-field verdicts from LLM response. Returns {field: {verdict, ...}}."""
    fields = raw.get("fields")
    if not fields or not isinstance(fields, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for field_name, field_data in fields.items():
        if not isinstance(field_data, dict):
            continue
        verdict = field_data.get("verdict", "unverifiable")
        if verdict not in VALID_VERDICTS:
            verdict = "unverifiable"
        result[field_name] = {**field_data, "verdict": verdict}
    return result


def compute_accuracy(field_results: dict[str, dict[str, Any]]) -> float | None:
    """Compute accuracy over verifiable fields. Returns None if no verifiable fields."""
    verifiable = [f for f in field_results.values() if f.get("verdict") in ("correct", "incorrect")]
    if not verifiable:
        return None
    correct = sum(1 for f in verifiable if f["verdict"] == "correct")
    return round(correct / len(verifiable), 2)


def detect_antibot(html: str) -> bool:
    """Check if HTML contains CAPTCHA or login wall indicators."""
    if not html:
        return False
    return bool(ANTIBOT_PATTERNS.search(html))


def _build_extracted_fields(listing: ListingObservation) -> dict[str, Any]:
    """Build the extracted fields dict to send to the LLM judge."""
    return {
        "title": listing.title,
        "price": float(listing.price) if listing.price else None,
        "currency": getattr(listing, "currency", "EUR"),
        "condition": listing.condition,
        "location": listing.location,
        "seller_rating": float(listing.seller_rating) if listing.seller_rating else None,
        "shipping_cost": float(listing.shipping_cost) if listing.shipping_cost else None,
        "is_sold": listing.is_sold,
    }


def _build_judge_prompt(extracted: dict[str, Any], has_screenshot: bool) -> str:
    """Build the LLM judge system prompt."""
    visual_line = "- A screenshot of a marketplace listing page\n" if has_screenshot else ""
    return (
        "You are a data quality auditor for a marketplace scraping system.\n\n"
        "You will be given:\n"
        f"{visual_line}"
        "- The raw HTML of the listing page\n"
        "- Fields extracted by our scraper\n\n"
        "Your task: for each extracted field, compare it against what you see on the page.\n\n"
        "Return a JSON object with this exact structure:\n"
        "{\n"
        '  "fields": {\n'
        '    "price": {"verdict": "correct|incorrect|unverifiable", "expected": "value from page", "extracted": "value from scraper"},\n'
        '    "title": {"verdict": "...", ...},\n'
        '    "condition": {"verdict": "...", ...},\n'
        '    "is_sold": {"verdict": "...", ...},\n'
        '    "location": {"verdict": "...", ...},\n'
        '    "seller_rating": {"verdict": "...", ...},\n'
        '    "shipping_cost": {"verdict": "...", ...}\n'
        "  },\n"
        '  "overall": "correct|partial_match|incorrect",\n'
        '  "notes": "Any observations about extraction quality"\n'
        "}\n\n"
        "Rules:\n"
        '- "correct" = extracted value matches page content (minor formatting differences OK)\n'
        '- "incorrect" = extracted value clearly wrong or missing when visible on page\n'
        '- "unverifiable" = field not visible on page or requires interaction to see\n'
        "- For price: currency must also match. Shipping cost included in price = incorrect.\n"
        "- For condition: match the marketplace's condition label, not your interpretation\n"
        '- For is_sold: look for "sold" badges, crossed-out prices, or "vendu" labels\n\n'
        "Extracted fields:\n"
        f"{json.dumps(extracted, ensure_ascii=False, indent=2)}\n\n"
        "Return ONLY valid JSON, no markdown fences."
    )


async def judge_listing(
    listing: ListingObservation,
    capture: AuditCapture,
) -> dict[str, Any]:
    """Run LLM judge on a single listing."""
    extracted = _build_extracted_fields(listing)

    if capture.html_snippet and detect_antibot(capture.html_snippet):
        blocked_results = {
            f: {"verdict": "unverifiable", "reason": "blocked_by_antibot"} for f in AUDITED_FIELDS
        }
        return {
            "field_results": blocked_results,
            "accuracy_score": None,
            "llm_response": {"blocked": True, "reason": "antibot_detected"},
            "cost_tokens": 0,
        }

    has_screenshot = capture.screenshot_path is not None
    prompt = _build_judge_prompt(extracted, has_screenshot)

    try:
        import google.generativeai as genai

        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not set")

        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)

        content_parts: list[Any] = []

        if capture.screenshot_path and os.path.exists(capture.screenshot_path):
            with open(capture.screenshot_path, "rb") as f:
                img_bytes = f.read()
            content_parts.append({"mime_type": "image/png", "data": img_bytes})

        if capture.html_snippet:
            snippet = capture.html_snippet[:50000]
            content_parts.append(f"Raw HTML of the listing page:\n\n{snippet}")

        content_parts.append(prompt)

        response = model.generate_content(
            content_parts,
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                response_mime_type="application/json",
            ),
        )

        raw_text = response.text.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        llm_response = json.loads(raw_text)
        cost_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            cost_tokens = getattr(response.usage_metadata, "total_token_count", 0)

    except Exception as exc:
        logger.error("LLM judge call failed for obs_id=%s: %s", listing.obs_id, exc)
        error_results = {
            f: {"verdict": "unverifiable", "reason": f"llm_error: {exc}"} for f in AUDITED_FIELDS
        }
        return {
            "field_results": error_results,
            "accuracy_score": None,
            "llm_response": {"error": str(exc)},
            "cost_tokens": 0,
        }

    field_results = parse_llm_verdict(llm_response)
    accuracy = compute_accuracy(field_results)

    return {
        "field_results": field_results,
        "accuracy_score": accuracy,
        "llm_response": llm_response,
        "cost_tokens": cost_tokens,
    }


async def capture_audit_batch(
    listings: list[ListingObservation],
    html_only: bool = False,
) -> dict[int, AuditCapture]:
    """Capture screenshot + HTML for a batch of listings using one Playwright browser."""
    import asyncio
    import random

    results: dict[int, AuditCapture] = {}
    listings_with_urls = [listing for listing in listings if listing.url]
    if not listings_with_urls:
        return results

    try:
        from playwright.async_api import async_playwright

        from libs.common.scraping import STEALTH_PATCH

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--webrtc-ip-handling-policy=disable_non_proxied_udp"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="fr-FR",
                timezone_id="Europe/Paris",
            )
            await context.add_init_script(STEALTH_PATCH)
            page = await context.new_page()

            for listing in listings_with_urls:
                try:
                    await page.goto(listing.url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)

                    html_content = await page.content()
                    html_snippet = html_content[:50000] if html_content else None

                    screenshot_path = None
                    if not html_only:
                        screenshots_dir = Path(settings.screenshot_storage_path) / "audit"
                        screenshots_dir.mkdir(parents=True, exist_ok=True)
                        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
                        screenshot_file = screenshots_dir / f"audit_{listing.obs_id}_{ts}.png"
                        await page.screenshot(
                            path=str(screenshot_file),
                            full_page=False,
                            clip={"x": 0, "y": 0, "width": 1920, "height": 3000},
                        )
                        screenshot_path = str(screenshot_file)

                    results[listing.obs_id] = AuditCapture(
                        screenshot_path=screenshot_path,
                        html_snippet=html_snippet,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to capture listing %s (%s): %s",
                        listing.obs_id,
                        listing.url,
                        exc,
                    )
                    results[listing.obs_id] = AuditCapture(screenshot_path=None, html_snippet=None)

                await asyncio.sleep(2 + random.random())  # noqa: S311

            await browser.close()

    except ImportError:
        logger.error("Playwright not installed — cannot capture audit pages")
    except Exception as exc:
        logger.error("Batch capture failed: %s", exc, exc_info=True)

    return results


async def audit_listings(
    listings: list[ListingObservation],
    audit_mode: str,
    ingestion_run_id: str | None = None,
    html_only: bool = False,
) -> list[ConnectorAudit]:
    """Full audit pipeline: capture pages -> judge each -> return records."""
    from libs.common.db import SessionLocal

    if audit_mode == "continuous":
        with SessionLocal() as db:
            from sqlalchemy import func as sa_func

            today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            used_tokens = (
                db.query(sa_func.coalesce(sa_func.sum(ConnectorAudit.cost_tokens), 0))
                .filter(ConnectorAudit.audited_at >= today_start)
                .scalar()
            )
            if used_tokens >= settings.audit_daily_token_budget:
                logger.warning(
                    "Daily audit token budget exhausted (%d/%d). Skipping.",
                    used_tokens,
                    settings.audit_daily_token_budget,
                )
                return []

    captures = await capture_audit_batch(listings, html_only=html_only)

    audit_records: list[ConnectorAudit] = []
    for listing in listings:
        capture = captures.get(listing.obs_id)
        if not capture or (not capture.screenshot_path and not capture.html_snippet):
            logger.warning("No content captured for obs_id=%s, skipping", listing.obs_id)
            continue

        result = await judge_listing(listing, capture)

        record = ConnectorAudit(
            ingestion_run_id=ingestion_run_id,
            obs_id=listing.obs_id,
            source=listing.source,
            audit_mode=audit_mode,
            screenshot_path=capture.screenshot_path,
            html_snippet=capture.html_snippet[:1000] if capture.html_snippet else None,
            llm_response=result["llm_response"],
            field_results=result["field_results"],
            accuracy_score=result["accuracy_score"],
            audited_at=datetime.now(UTC),
            cost_tokens=result["cost_tokens"],
        )
        audit_records.append(record)

    return audit_records


def compute_connector_accuracy(
    audit_records: list[ConnectorAudit],
) -> dict[str, dict[str, Any]]:
    """Aggregate accuracy stats per connector from audit records."""
    by_source: dict[str, list[ConnectorAudit]] = defaultdict(list)
    for r in audit_records:
        by_source[r.source].append(r)

    result: dict[str, dict[str, Any]] = {}
    for source, records in by_source.items():
        scores = [float(r.accuracy_score) for r in records if r.accuracy_score is not None]
        avg_accuracy = sum(scores) / len(scores) if scores else None

        field_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"correct": 0, "incorrect": 0, "unverifiable": 0}
        )
        for r in records:
            if not r.field_results:
                continue
            for field_name, field_data in r.field_results.items():
                verdict = field_data.get("verdict", "unverifiable")
                if verdict in field_stats[field_name]:
                    field_stats[field_name][verdict] += 1

        per_field: dict[str, float | None] = {}
        for field_name, stats in field_stats.items():
            verifiable = stats["correct"] + stats["incorrect"]
            per_field[field_name] = (
                round(stats["correct"] / verifiable, 2) if verifiable > 0 else None
            )

        if avg_accuracy is None:
            status = "unknown"
        elif avg_accuracy >= settings.audit_accuracy_green:
            status = "green"
        elif avg_accuracy >= settings.audit_accuracy_yellow:
            status = "yellow"
        else:
            status = "red"

        result[source] = {
            "accuracy": round(avg_accuracy, 3) if avg_accuracy is not None else None,
            "sample_size": len(records),
            "per_field": per_field,
            "status": status,
        }

    return result

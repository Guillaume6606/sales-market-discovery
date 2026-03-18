"""CLI for running full connector data quality audits with detailed reports."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.orm import sessionmaker

from ingestion.audit import audit_listings, compute_connector_accuracy
from libs.common.db import SessionLocal, engine
from libs.common.models import ConnectorAudit, ListingObservation, ProductTemplate
from libs.common.settings import settings

# Session factory with expire_on_commit=False so detached ORM objects
# remain accessible outside the session scope (needed for audit pipeline).
_AuditSession = sessionmaker(bind=engine, expire_on_commit=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connector data quality audit")
    parser.add_argument(
        "--connectors",
        default="ebay,leboncoin,vinted",
        help="Comma-separated connectors (default: all)",
    )
    parser.add_argument(
        "--products-per-connector",
        type=int,
        default=5,
        help="Products to test per connector (default: 5)",
    )
    parser.add_argument(
        "--listings-per-product",
        type=int,
        default=20,
        help="Ingestion limit per product (default: 20)",
    )
    parser.add_argument("--output-dir", default=None, help="Report output directory")
    parser.add_argument(
        "--skip-ingestion", action="store_true", help="Audit existing recent listings"
    )
    parser.add_argument("--product-ids", default=None, help="Specific product IDs, comma-separated")
    parser.add_argument(
        "--html-only", action="store_true", help="Skip screenshots, judge from HTML only"
    )
    parser.add_argument(
        "--max-consecutive-per-domain",
        type=int,
        default=5,
        help="Max consecutive requests per domain before cooling pause (default: 5)",
    )
    return parser.parse_args()


async def _run_ingestion_for_audit(
    connectors: list[str],
    product_ids: list[str],
    listings_per_product: int,
) -> dict[str, list[ListingObservation]]:
    """Run fresh ingestion and return listings by connector."""
    from ingestion.ingestion import run_full_ingestion

    results: dict[str, list[ListingObservation]] = defaultdict(list)
    source_map = {
        "ebay": {"ebay_sold": listings_per_product, "ebay_listings": listings_per_product},
        "leboncoin": {
            "leboncoin_listings": listings_per_product,
        },
        "vinted": {"vinted_listings": listings_per_product},
    }

    ingestion_start = datetime.now(UTC)

    for product_id in product_ids:
        for connector in connectors:
            limits = source_map.get(connector, {})
            try:
                logger.info("Ingesting %s for product %s", connector, product_id)
                await run_full_ingestion(product_id, limits, sources=[connector])
            except Exception as exc:
                logger.error("Ingestion failed for %s/%s: %s", connector, product_id, exc)

    with _AuditSession() as db:
        for connector in connectors:
            listings = (
                db.query(ListingObservation)
                .filter(
                    ListingObservation.source == connector,
                    ListingObservation.url.isnot(None),
                    ListingObservation.product_id.in_(product_ids),
                    ListingObservation.last_seen_at >= ingestion_start,
                )
                .order_by(ListingObservation.last_seen_at.desc())
                .limit(500)
                .all()
            )
            results[connector] = listings

    return results


def _get_recent_listings(
    connectors: list[str],
    limit_per_connector: int = 100,
) -> dict[str, list[ListingObservation]]:
    """Fetch recent listings from DB for --skip-ingestion mode."""
    results: dict[str, list[ListingObservation]] = {}
    with _AuditSession() as db:
        for connector in connectors:
            listings = (
                db.query(ListingObservation)
                .filter(
                    ListingObservation.source == connector,
                    ListingObservation.url.isnot(None),
                )
                .order_by(ListingObservation.last_seen_at.desc())
                .limit(limit_per_connector)
                .all()
            )
            results[connector] = listings
    return results


def _generate_connector_report(
    source: str,
    records: list[ConnectorAudit],
    accuracy_data: dict[str, Any],
) -> str:
    """Generate detailed Markdown report for a single connector."""
    accuracy = accuracy_data.get("accuracy")
    status = accuracy_data.get("status", "unknown")
    threshold = settings.audit_accuracy_yellow

    verdict = "PASS" if status in ("green", "yellow") else "FAIL" if status == "red" else "UNKNOWN"
    verdict_icon = "✅" if verdict == "PASS" else "❌" if verdict == "FAIL" else "❓"

    verifiable_count = sum(1 for r in records if r.accuracy_score is not None)

    lines = [
        f"# {source.title()} Connector Audit — {datetime.now(UTC).strftime('%Y-%m-%d')}",
        "",
        "## Summary",
        f"- Listings audited: {len(records)}",
        f"- Listings with verifiable fields: {verifiable_count}/{len(records)}",
        f"- Overall accuracy: {accuracy:.1%}"
        if accuracy is not None
        else "- Overall accuracy: N/A",
        f"- Verdict: {verdict_icon} {verdict} (threshold: {threshold:.0%})",
        "",
        "## Per-Field Accuracy",
        "",
        "| Field | Correct | Incorrect | Unverifiable | Accuracy |",
        "|-------|---------|-----------|--------------|----------|",
    ]

    field_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"correct": 0, "incorrect": 0, "unverifiable": 0}
    )
    for r in records:
        if not r.field_results:
            continue
        for fname, fdata in r.field_results.items():
            v = fdata.get("verdict", "unverifiable")
            if v in field_counts[fname]:
                field_counts[fname][v] += 1

    for fname in sorted(field_counts.keys()):
        counts = field_counts[fname]
        verifiable = counts["correct"] + counts["incorrect"]
        acc = f"{counts['correct'] / verifiable:.1%}" if verifiable > 0 else "N/A"
        lines.append(
            f"| {fname} | {counts['correct']} | {counts['incorrect']} "
            f"| {counts['unverifiable']} | {acc} |"
        )

    lines.extend(["", "## Failure Analysis", ""])
    for fname in sorted(field_counts.keys()):
        counts = field_counts[fname]
        if counts["incorrect"] == 0:
            continue
        verifiable = counts["correct"] + counts["incorrect"]
        acc = counts["correct"] / verifiable if verifiable > 0 else 0
        lines.extend([f"### {fname} — {acc:.1%} accuracy", ""])

        failures = []
        for r in records:
            if not r.field_results:
                continue
            fd = r.field_results.get(fname, {})
            if fd.get("verdict") == "incorrect":
                failures.append(
                    {
                        "obs_id": r.obs_id,
                        "expected": fd.get("expected", "?"),
                        "extracted": fd.get("extracted", "?"),
                    }
                )

        if failures:
            lines.append("| Listing | Expected | Extracted |")
            lines.append("|---------|----------|-----------|")
            for f in failures[:10]:
                lines.append(f"| {f['obs_id']} | {f['expected']} | {f['extracted']} |")
            lines.append("")

    notes = [
        r.llm_response.get("notes", "")
        for r in records
        if r.llm_response and r.llm_response.get("notes")
    ]
    if notes:
        lines.extend(["## LLM Notes", ""])
        for note in set(notes):
            if note:
                lines.append(f"- {note}")
        lines.append("")

    lines.extend(
        [
            "## Raw Data",
            "",
            "<details>",
            f"<summary>Full audit results ({len(records)} listings)</summary>",
            "",
        ]
    )

    sorted_fields = sorted(field_counts.keys())
    if sorted_fields:
        lines.append("| obs_id | accuracy | " + " | ".join(sorted_fields) + " |")
        lines.append("|--------|----------|" + "|".join(["---"] * len(sorted_fields)) + "|")
        for r in records:
            acc_str = f"{r.accuracy_score:.0%}" if r.accuracy_score is not None else "N/A"
            field_verdicts = []
            for fn in sorted_fields:
                fd = (r.field_results or {}).get(fn, {})
                v = fd.get("verdict", "?")
                icon = "✓" if v == "correct" else "✗" if v == "incorrect" else "?"
                field_verdicts.append(icon)
            lines.append(f"| {r.obs_id} | {acc_str} | " + " | ".join(field_verdicts) + " |")

    lines.extend(["", "</details>", ""])
    return "\n".join(lines)


def _generate_summary_report(
    all_accuracy: dict[str, dict[str, Any]],
    all_records: dict[str, list[ConnectorAudit]],
) -> str:
    """Generate cross-connector summary report."""
    lines = [
        f"# Connector Audit Summary — {datetime.now(UTC).strftime('%Y-%m-%d')}",
        "",
        "## Overall Results",
        "",
        "| Connector | Accuracy | Listings | Status |",
        "|-----------|----------|----------|--------|",
    ]

    for source in sorted(all_accuracy.keys()):
        data = all_accuracy[source]
        acc = f"{data['accuracy']:.1%}" if data["accuracy"] is not None else "N/A"
        status_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(data["status"], "❓")
        lines.append(
            f"| {source} | {acc} | {data['sample_size']} | {status_icon} {data['status']} |"
        )

    lines.extend(["", "## Per-Field Accuracy (All Connectors)", ""])

    all_fields: dict[str, dict[str, float | None]] = {}
    for source, data in all_accuracy.items():
        for fname, acc in data.get("per_field", {}).items():
            if fname not in all_fields:
                all_fields[fname] = {}
            all_fields[fname][source] = acc

    if all_fields:
        headers = sorted(all_accuracy.keys())
        lines.append("| Field | " + " | ".join(headers) + " |")
        lines.append("|-------|" + "|".join(["---"] * len(headers)) + "|")
        for fname in sorted(all_fields.keys()):
            vals = []
            for h in headers:
                v = all_fields[fname].get(h)
                vals.append(f"{v:.0%}" if v is not None else "N/A")
            lines.append(f"| {fname} | " + " | ".join(vals) + " |")

    lines.append("")
    return "\n".join(lines)


async def main() -> None:
    args = _parse_args()
    connectors = [c.strip() for c in args.connectors.split(",")]
    output_dir = Path(
        args.output_dir or f"reports/connector-audit-{datetime.now(UTC).strftime('%Y-%m-%d')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Starting connector audit: connectors=%s, output=%s", connectors, output_dir)

    if args.skip_ingestion:
        logger.info("Skipping ingestion — auditing existing listings")
        listings_by_connector = _get_recent_listings(connectors)
    else:
        if args.product_ids:
            product_ids = [p.strip() for p in args.product_ids.split(",")]
        else:
            with SessionLocal() as db:
                products = (
                    db.query(ProductTemplate)
                    .filter(ProductTemplate.is_active.is_(True))
                    .limit(args.products_per_connector)
                    .all()
                )
                product_ids = [str(p.product_id) for p in products]

        listings_by_connector = await _run_ingestion_for_audit(
            connectors,
            product_ids,
            args.listings_per_product,
        )

    all_records: dict[str, list[ConnectorAudit]] = {}
    for connector, listings in listings_by_connector.items():
        if not listings:
            logger.warning("No listings for connector %s", connector)
            continue
        logger.info("Auditing %d listings for %s", len(listings), connector)
        records = await audit_listings(
            listings,
            audit_mode="cli",
            html_only=args.html_only,
            max_consecutive_per_domain=args.max_consecutive_per_domain,
        )

        with _AuditSession() as db:
            for r in records:
                db.add(r)
            db.commit()

        all_records[connector] = records

    all_flat = [r for records in all_records.values() for r in records]
    all_accuracy = compute_connector_accuracy(all_flat)

    for connector, records in all_records.items():
        if connector not in all_accuracy:
            continue
        report = _generate_connector_report(connector, records, all_accuracy[connector])
        (output_dir / f"{connector}.md").write_text(report)
        logger.info("Report written: %s/%s.md", output_dir, connector)

    summary = _generate_summary_report(all_accuracy, all_records)
    (output_dir / "summary.md").write_text(summary)
    logger.info("Summary written: %s/summary.md", output_dir)

    print(f"\n{'=' * 60}")
    print(f"Audit complete. Reports in: {output_dir}")
    print(f"{'=' * 60}")
    for source, data in all_accuracy.items():
        acc = f"{data['accuracy']:.1%}" if data["accuracy"] is not None else "N/A"
        print(f"  {source}: {acc} ({data['status']})")
    print()


if __name__ == "__main__":
    asyncio.run(main())

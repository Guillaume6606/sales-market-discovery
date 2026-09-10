"""Inspect legacy LeBonCoin sold proxies; apply only after saving an audit archive.

uv run python scripts/quarantine_sold_proxies.py
uv run python scripts/quarantine_sold_proxies.py --apply --archive /path/to/new-audit.json
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from libs.common.db import SessionLocal
from libs.common.models import ListingObservation, ListingScore, MarketPriceNormal, PMNHistory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.apply and args.archive is None:
        parser.error("--apply requires a new --archive path")
    with SessionLocal() as db:
        rows = (
            db.query(ListingObservation)
            .filter(
                ListingObservation.source == "leboncoin",
                ListingObservation.is_sold.is_(True),
                ListingObservation.evidence_type != "verified_sale",
            )
            .all()
        )
        pids = list({row.product_id for row in rows})
        pmns = (
            db.query(MarketPriceNormal).filter(MarketPriceNormal.product_id.in_(pids)).all()
            if pids
            else []
        )
        history = db.query(PMNHistory).filter(PMNHistory.product_id.in_(pids)).all() if pids else []
        scores = (
            db.query(ListingScore).filter(ListingScore.product_id.in_(pids)).all() if pids else []
        )
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry_run",
                    "observations": len(rows),
                    "products": len(pids),
                    "scores": len(scores),
                }
            )
        )
        if not args.apply or not rows:
            return

        def snapshot(objects: list) -> list[dict]:
            return [
                {column.name: getattr(row, column.name) for column in row.__table__.columns}
                for row in objects
            ]

        archive = {
            "recorded_at": datetime.now(UTC),
            "observations": snapshot(rows),
            "pmn": snapshot(pmns),
            "history": snapshot(history),
            "scores": snapshot(scores),
        }
        with args.archive.open("x") as output:
            json.dump(archive, output, default=str, indent=2)
        for row in rows:
            row.is_sold = False
            row.is_stale = True
            row.evidence_type = "unknown"
        for row in pmns:
            row.confidence = 0
            row.methodology = {
                **(row.methodology or {}),
                "verified_sales": False,
                "quarantined": True,
            }
        for row in history:
            row.is_valid = False
        for row in scores:
            row.arbitrage_spread_eur = None
            row.net_roi_pct = None
            row.risk_adjusted_confidence = 0
            row.score_breakdown = {"quarantined": True, "archive": str(args.archive)}
        db.commit()


if __name__ == "__main__":
    main()

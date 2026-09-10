import json
from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from libs.common.models import ListingObservation, ListingScore, MarketPriceNormal
from scripts import quarantine_sold_proxies


def test_quarantine_is_dry_run_first_and_archives_before_changes(
    integration_db, seed_product, monkeypatch, tmp_path
):
    now = datetime.now(UTC)
    proxy = ListingObservation(
        product_id=seed_product,
        source="leboncoin",
        listing_id="proxy",
        price=100,
        currency="EUR",
        is_sold=True,
        evidence_type="unknown",
        observed_at=now,
    )
    verified = ListingObservation(
        product_id=seed_product,
        source="leboncoin",
        listing_id="verified",
        price=120,
        currency="EUR",
        is_sold=True,
        evidence_type="verified_sale",
        observed_at=now,
    )
    integration_db.add_all(
        [proxy, verified, MarketPriceNormal(product_id=seed_product, pmn=110, confidence=0.9)]
    )
    integration_db.flush()
    integration_db.add(
        ListingScore(obs_id=proxy.obs_id, product_id=seed_product, arbitrage_spread_eur=50)
    )
    integration_db.commit()
    monkeypatch.setattr(
        quarantine_sold_proxies, "SessionLocal", sessionmaker(bind=integration_db.get_bind())
    )
    monkeypatch.setattr("sys.argv", ["quarantine"])
    quarantine_sold_proxies.main()
    integration_db.refresh(proxy)
    assert proxy.is_sold
    archive = tmp_path / "quarantine.json"
    monkeypatch.setattr("sys.argv", ["quarantine", "--apply", "--archive", str(archive)])
    quarantine_sold_proxies.main()
    recorded = json.loads(archive.read_text())
    assert recorded["observations"][0]["is_sold"] is True
    assert len(recorded["observations"]) == 1
    integration_db.expire_all()
    assert not proxy.is_sold and proxy.is_stale
    assert verified.is_sold
    assert integration_db.query(ListingScore).one().arbitrage_spread_eur is None
    assert integration_db.query(MarketPriceNormal).one().confidence == 0

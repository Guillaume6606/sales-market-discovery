"""Integration test: PMN computation with real SQLite database."""

from libs.common.models import MarketPriceNormal, PMNHistory


class TestPmnComputation:
    def test_compute_pmn_creates_records(
        self, integration_db, seed_product, seed_sold_observations
    ):
        """Seeding 20 sold observations → compute_pmn_for_product creates PMN + history."""
        from ingestion.computation import compute_pmn_for_product

        result = compute_pmn_for_product(seed_product, db=integration_db)

        assert result["status"] == "success"
        assert result["pmn"] is not None
        assert result["sample_size"] > 0

        # Verify MarketPriceNormal row
        pmn_row = (
            integration_db.query(MarketPriceNormal)
            .filter(MarketPriceNormal.product_id == seed_product)
            .first()
        )
        assert pmn_row is not None
        assert float(pmn_row.pmn) > 0

        # Verify PMNHistory row
        history_row = (
            integration_db.query(PMNHistory).filter(PMNHistory.product_id == seed_product).first()
        )
        assert history_row is not None
        assert float(history_row.pmn) == float(pmn_row.pmn)

    def test_pmn_value_reasonable(self, integration_db, seed_product, seed_sold_observations):
        """PMN should be within the range of seeded prices (700-795)."""
        from ingestion.computation import compute_pmn_for_product

        result = compute_pmn_for_product(seed_product, db=integration_db)
        assert 650.0 <= result["pmn"] <= 850.0

    def test_insufficient_data(self, integration_db, seed_product):
        """No observations → insufficient_data."""
        from ingestion.computation import compute_pmn_for_product

        result = compute_pmn_for_product(seed_product, db=integration_db)
        assert result["status"] == "insufficient_data"

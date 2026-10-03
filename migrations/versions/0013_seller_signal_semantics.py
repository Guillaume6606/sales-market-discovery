"""Clear historical seller feedback and listing counts used as transaction evidence."""

from alembic import op

revision = "0013_seller_signal_semantics"
down_revision = "0012_listing_vision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE listing_observation SET seller_rating = NULL "
        "WHERE source = 'ebay' AND seller_rating IS NOT NULL"
    )
    op.execute(
        "UPDATE listing_detail AS detail SET seller_transaction_count = NULL "
        "FROM listing_observation AS observation "
        "WHERE detail.obs_id = observation.obs_id "
        "AND observation.source IN ('ebay', 'leboncoin', 'vinted') "
        "AND detail.seller_transaction_count IS NOT NULL"
    )


def downgrade() -> None:
    """Irreversible cleanup: only a pre-migration backup can restore discarded proxies."""

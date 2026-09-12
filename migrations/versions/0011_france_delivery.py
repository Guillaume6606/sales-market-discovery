"""Record explicit France delivery evidence without inferring historical eligibility."""

import sqlalchemy as sa
from alembic import op

revision = "0011_france_delivery"
down_revision = "0010_trade_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listing_observation", sa.Column("delivery_to_france", sa.Boolean()))
    op.add_column("listing_observation", sa.Column("delivery_evidence", sa.Text()))
    for name, kind in (
        ("reviewed_delivery_to_france", sa.Boolean()),
        ("delivery_evidence", sa.Text()),
        ("raw_delivery_to_france", sa.Boolean()),
        ("raw_delivery_evidence", sa.Text()),
        ("raw_url", sa.Text()),
    ):
        op.add_column("valuation_listing_review", sa.Column(name, kind))


def downgrade() -> None:
    for name in (
        "reviewed_delivery_to_france",
        "delivery_evidence",
        "raw_delivery_to_france",
        "raw_delivery_evidence",
        "raw_url",
    ):
        op.drop_column("valuation_listing_review", name)
    op.drop_column("listing_observation", "delivery_evidence")
    op.drop_column("listing_observation", "delivery_to_france")

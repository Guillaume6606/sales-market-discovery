"""Durable vision cache and atomic monetary reservations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_listing_vision"
down_revision = "0011_france_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listing_observation", sa.Column("vision_result", JSONB))
    op.add_column(
        "listing_observation", sa.Column("vision_checked_at", sa.TIMESTAMP(timezone=True))
    )
    op.create_table(
        "vision_request",
        sa.Column("request_key", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("owner_token", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("result", JSONB),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "vision_budget",
        sa.Column("period", sa.Text(), primary_key=True),
        sa.Column("currency", sa.Text(), primary_key=True),
        sa.Column("reserved", sa.Numeric(18, 8), nullable=False, server_default="0"),
        sa.CheckConstraint("reserved >= 0"),
    )
    op.create_table(
        "vision_attempt",
        sa.Column("attempt_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "request_key", sa.Text(), sa.ForeignKey("vision_request.request_key"), nullable=False
        ),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("reserved", sa.Numeric(18, 8), nullable=False),
        sa.Column("metadata_json", JSONB),
        sa.Column("actual_cost", sa.Numeric(18, 8)),
        sa.Column("reconciled_at", sa.TIMESTAMP(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("listing_observation", "vision_checked_at")
    op.drop_column("listing_observation", "vision_result")
    op.drop_table("vision_attempt")
    op.drop_table("vision_budget")
    op.drop_table("vision_request")

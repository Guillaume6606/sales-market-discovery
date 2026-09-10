"""Preserve observation provenance and support durable delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0008_profit_foundations"
down_revision = "0007_enrichment_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pmn_history",
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "product_template",
        sa.Column("ingestion_interval_minutes", sa.Integer(), nullable=False, server_default="60"),
    )
    for name in ("first_seen_at", "updated_at"):
        op.add_column("listing_observation", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column(
        "listing_observation",
        sa.Column("evidence_type", sa.Text(), nullable=False, server_default="unknown"),
    )
    op.execute(
        "UPDATE listing_observation SET first_seen_at=observed_at, updated_at=COALESCE(last_seen_at, observed_at), evidence_type=CASE WHEN is_sold THEN 'unknown' ELSE 'asking' END"
    )
    op.alter_column("listing_observation", "evidence_type", server_default="asking")
    op.create_table(
        "listing_observation_event",
        sa.Column("event_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "obs_id", sa.BigInteger(), sa.ForeignKey("listing_observation.obs_id"), nullable=False
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric()),
        sa.Column("currency", sa.Text()),
        sa.Column("is_sold", sa.Boolean(), nullable=False),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON()),
    )
    op.create_index("ix_listing_observation_event_obs_id", "listing_observation_event", ["obs_id"])
    op.add_column("alert_event", sa.Column("idempotency_key", sa.Text()))
    op.create_unique_constraint(
        "uq_alert_event_idempotency_key", "alert_event", ["idempotency_key"]
    )
    op.add_column(
        "alert_event",
        sa.Column("delivery_status", sa.Text(), nullable=False, server_default="pending"),
    )
    op.add_column(
        "alert_event",
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("alert_event", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column(
        "alert_event",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Historical delivery outcome is uncertain: never automatically replay old messages.
    op.execute(
        "UPDATE alert_event SET delivery_status='legacy', created_at=COALESCE(sent_at, now())"
    )


def downgrade() -> None:
    op.drop_column("pmn_history", "is_valid")
    for name in (
        "created_at",
        "next_attempt_at",
        "delivery_attempts",
        "delivery_status",
        "idempotency_key",
    ):
        op.drop_column("alert_event", name)
    op.drop_table("listing_observation_event")
    for name in ("evidence_type", "updated_at", "first_seen_at"):
        op.drop_column("listing_observation", name)
    op.drop_column("product_template", "ingestion_interval_minutes")

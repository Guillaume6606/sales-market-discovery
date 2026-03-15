import sqlalchemy as sa
from alembic import op

revision = "0002_week1_foundations"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade():
    # Create ingestion_run table
    op.create_table(
        "ingestion_run",
        sa.Column("run_id", sa.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("product_id", sa.UUID, sa.ForeignKey("product_template.product_id")),
        sa.Column("source", sa.Text),
        sa.Column("function_name", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("duration_s", sa.Numeric),
        sa.Column("listings_fetched", sa.Integer),
        sa.Column("listings_deduped", sa.Integer),
        sa.Column("listings_persisted", sa.Integer),
        sa.Column("filtering_stats", sa.JSON),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    # Create alert_feedback table
    op.create_table(
        "alert_feedback",
        sa.Column(
            "feedback_id", sa.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("alert_id", sa.BigInteger, sa.ForeignKey("alert_event.alert_id")),
        sa.Column("feedback", sa.Text, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "feedback IN ('interested', 'not_interested', 'purchased')",
            name="ck_alert_feedback_valid",
        ),
    )

    # Add columns to listing_observation
    op.add_column("listing_observation", sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)))
    op.add_column(
        "listing_observation",
        sa.Column("is_stale", sa.Boolean, server_default=sa.text("false")),
    )

    # Remove historical duplicates before enforcing unique constraint.
    # Keep the row with the highest obs_id (most recent insert) for each
    # (source, listing_id, product_id) group.
    op.execute(
        sa.text("""
            DELETE FROM listing_observation
            WHERE obs_id NOT IN (
                SELECT MAX(obs_id)
                FROM listing_observation
                GROUP BY source, listing_id, product_id
            )
        """)
    )

    # Add unique constraint on listing_observation
    op.create_unique_constraint(
        "uq_listing_source_product",
        "listing_observation",
        ["source", "listing_id", "product_id"],
    )


def downgrade():
    op.drop_constraint("uq_listing_source_product", "listing_observation", type_="unique")
    op.drop_column("listing_observation", "is_stale")
    op.drop_column("listing_observation", "last_seen_at")
    op.drop_table("alert_feedback")
    op.drop_table("ingestion_run")

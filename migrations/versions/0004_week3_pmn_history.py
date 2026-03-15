import sqlalchemy as sa
from alembic import op

revision = "0004_week3_pmn_history"
down_revision = "0003_week2_confidence"
branch_labels = None
depends_on = None


def upgrade():
    # New table: PMN computation history for backtesting
    op.create_table(
        "pmn_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "product_id",
            sa.UUID,
            sa.ForeignKey("product_template.product_id"),
            nullable=False,
        ),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("pmn", sa.Numeric),
        sa.Column("pmn_low", sa.Numeric),
        sa.Column("pmn_high", sa.Numeric),
        sa.Column("confidence", sa.Numeric),
        sa.Column("sample_size", sa.Integer),
    )
    op.create_index(
        "ix_pmn_history_product_computed",
        "pmn_history",
        ["product_id", "computed_at"],
    )

    # Alter alert_feedback (created in 0002): add unique constraint + updated_at
    op.create_unique_constraint("uq_alert_feedback_alert_id", "alert_feedback", ["alert_id"])
    op.add_column(
        "alert_feedback",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade():
    op.drop_column("alert_feedback", "updated_at")
    op.drop_constraint("uq_alert_feedback_alert_id", "alert_feedback", type_="unique")
    op.drop_index("ix_pmn_history_product_computed", table_name="pmn_history")
    op.drop_table("pmn_history")

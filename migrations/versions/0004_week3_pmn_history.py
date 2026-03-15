import sqlalchemy as sa
from alembic import op

revision = "0004_week3_pmn_history"
down_revision = "0003_week2_confidence"
branch_labels = None
depends_on = None


def upgrade():
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

    op.create_table(
        "alert_feedback",
        sa.Column(
            "feedback_id",
            sa.UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "alert_id",
            sa.BigInteger,
            sa.ForeignKey("alert_event.alert_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("feedback", sa.Text, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "feedback IN ('interested', 'not_interested', 'purchased')",
            name="ck_alert_feedback_valid",
        ),
    )


def downgrade():
    op.drop_table("alert_feedback")
    op.drop_index("ix_pmn_history_product_computed", table_name="pmn_history")
    op.drop_table("pmn_history")

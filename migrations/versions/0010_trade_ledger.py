"""Create the private operator trade ledger and monthly overhead table."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0010_trade_ledger"
down_revision = "0009_verified_valuation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade",
        sa.Column(
            "trade_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "observation_id",
            sa.BigInteger,
            sa.ForeignKey("listing_observation.obs_id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("buy_platform", sa.Text, nullable=False),
        sa.Column("exit_platform", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="purchased"),
        sa.Column("acquired_on", sa.Date, nullable=False),
        sa.Column("sold_on", sa.Date),
        sa.Column("settled_on", sa.Date),
        sa.Column("closed_on", sa.Date),
        sa.Column("acquisition_price_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("acquisition_fees_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("inbound_logistics_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("repair_cost_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("selling_fees_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("outbound_logistics_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("refund_cost_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("turnover_charges_eur", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("sale_revenue_eur", sa.Numeric(12, 2)),
        sa.Column("forecast_sale_revenue_eur", sa.Numeric(12, 2)),
        sa.Column("forecast_remaining_costs_eur", sa.Numeric(12, 2)),
        sa.Column("time_spent_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("notes", sa.Text),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('purchased', 'listed', 'sold', 'settled', 'returned', 'written_off')",
            name="ck_trade_status",
        ),
        sa.CheckConstraint("acquisition_price_eur >= 0", name="ck_trade_acquisition_nonnegative"),
        sa.CheckConstraint(
            "acquisition_fees_eur >= 0", name="ck_trade_acquisition_fees_nonnegative"
        ),
        sa.CheckConstraint(
            "inbound_logistics_eur >= 0", name="ck_trade_inbound_logistics_nonnegative"
        ),
        sa.CheckConstraint("repair_cost_eur >= 0", name="ck_trade_repairs_nonnegative"),
        sa.CheckConstraint("selling_fees_eur >= 0", name="ck_trade_selling_fees_nonnegative"),
        sa.CheckConstraint(
            "outbound_logistics_eur >= 0", name="ck_trade_outbound_logistics_nonnegative"
        ),
        sa.CheckConstraint("refund_cost_eur >= 0", name="ck_trade_refunds_nonnegative"),
        sa.CheckConstraint("turnover_charges_eur >= 0", name="ck_trade_turnover_nonnegative"),
        sa.CheckConstraint("sale_revenue_eur >= 0", name="ck_trade_revenue_nonnegative"),
        sa.CheckConstraint(
            "forecast_sale_revenue_eur >= 0",
            name="ck_trade_forecast_revenue_nonnegative",
        ),
        sa.CheckConstraint(
            "forecast_remaining_costs_eur >= 0",
            name="ck_trade_forecast_costs_nonnegative",
        ),
        sa.CheckConstraint("time_spent_minutes >= 0", name="ck_trade_time_nonnegative"),
        sa.CheckConstraint("sold_on IS NULL OR sold_on >= acquired_on", name="ck_trade_sold_date"),
        sa.CheckConstraint(
            "settled_on IS NULL OR (sold_on IS NOT NULL AND settled_on >= sold_on)",
            name="ck_trade_settled_date",
        ),
        sa.CheckConstraint(
            "closed_on IS NULL OR (closed_on >= acquired_on AND "
            "(sold_on IS NULL OR closed_on >= sold_on))",
            name="ck_trade_closed_date",
        ),
    )
    op.create_index("ix_trade_status_acquired", "trade", ["status", "acquired_on"])
    op.create_index("ix_trade_settled_on", "trade", ["settled_on"])
    op.create_index("ix_trade_closed_on", "trade", ["closed_on"])
    op.create_index("ix_trade_observation_id", "trade", ["observation_id"])

    op.create_table(
        "monthly_overhead",
        sa.Column("month", sa.Date, primary_key=True),
        sa.Column("amount_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("amount_eur >= 0", name="ck_monthly_overhead_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("monthly_overhead")
    op.drop_index("ix_trade_observation_id", table_name="trade")
    op.drop_index("ix_trade_closed_on", table_name="trade")
    op.drop_index("ix_trade_settled_on", table_name="trade")
    op.drop_index("ix_trade_status_acquired", table_name="trade")
    op.drop_table("trade")

"""Add manually verified valuation references."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0009_verified_valuation"
down_revision = "0008_profit_foundations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verified_valuation_reference",
        sa.Column(
            "reference_id",
            UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "product_id",
            UUID,
            sa.ForeignKey("product_template.product_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purchase_source", sa.Text, nullable=False),
        sa.Column("destination_marketplace", sa.Text, nullable=False),
        sa.Column("required_tokens", ARRAY(sa.Text), nullable=False),
        sa.Column("excluded_tokens", ARRAY(sa.Text), nullable=False),
        sa.Column("condition", sa.Text, nullable=False),
        sa.Column("reviewed_price_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.Text, nullable=False),
        sa.Column("comparable_sold_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.Text, nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("limitations", sa.Text, nullable=False),
        sa.Column("purchase_fee_rate", sa.Numeric(8, 6), nullable=False),
        sa.Column("purchase_fee_fixed_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("sell_fee_rate", sa.Numeric(8, 6), nullable=False),
        sa.Column("sell_fee_fixed_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("social_charge_rate", sa.Numeric(8, 6), nullable=False),
        sa.Column("outbound_logistics_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("risk_allowance_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("minimum_contribution_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
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
        sa.CheckConstraint("currency = 'EUR'", name="ck_valuation_reference_currency_eur"),
        sa.CheckConstraint(
            "condition IN ('new', 'like_new', 'good', 'fair')",
            name="ck_valuation_reference_condition",
        ),
        sa.CheckConstraint("reviewed_price_eur > 0", name="ck_valuation_reference_price_positive"),
        sa.CheckConstraint(
            "purchase_fee_rate >= 0 AND purchase_fee_rate <= 1",
            name="ck_valuation_reference_purchase_rate",
        ),
        sa.CheckConstraint(
            "sell_fee_rate >= 0 AND sell_fee_rate <= 1",
            name="ck_valuation_reference_sell_rate",
        ),
        sa.CheckConstraint(
            "social_charge_rate >= 0 AND social_charge_rate <= 1",
            name="ck_valuation_reference_social_rate",
        ),
        sa.CheckConstraint(
            "purchase_fee_fixed_eur >= 0 AND sell_fee_fixed_eur >= 0 "
            "AND outbound_logistics_eur >= 0 AND risk_allowance_eur >= 0 "
            "AND minimum_contribution_eur >= 0",
            name="ck_valuation_reference_costs_nonnegative",
        ),
        sa.CheckConstraint(
            "comparable_sold_at <= reviewed_at AND reviewed_at < expires_at",
            name="ck_valuation_reference_evidence_window",
        ),
    )
    op.create_index(
        "ix_valuation_reference_product_source_active",
        "verified_valuation_reference",
        ["product_id", "purchase_source", "is_active"],
    )
    op.create_index(
        "ix_valuation_reference_expiry",
        "verified_valuation_reference",
        ["expires_at"],
    )
    op.create_index(
        "ix_valuation_reference_reviewed",
        "verified_valuation_reference",
        ["reviewed_at"],
    )
    op.create_table(
        "valuation_listing_review",
        sa.Column(
            "review_id",
            UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "obs_id",
            sa.BigInteger,
            sa.ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewed_title", sa.Text, nullable=False),
        sa.Column("reviewed_condition", sa.Text, nullable=False),
        sa.Column("reviewed_shipping_cost_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("reviewed_by", sa.Text, nullable=False),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("notes", sa.Text, nullable=False),
        sa.Column("raw_title", sa.Text, nullable=False),
        sa.Column("raw_price_eur", sa.Numeric(12, 2), nullable=False),
        sa.Column("raw_condition", sa.Text),
        sa.Column("raw_shipping_cost_eur", sa.Numeric(12, 2)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "reviewed_condition IN ('new', 'like_new', 'good', 'fair')",
            name="ck_valuation_listing_review_condition",
        ),
        sa.CheckConstraint(
            "reviewed_shipping_cost_eur >= 0",
            name="ck_valuation_listing_review_shipping_nonnegative",
        ),
        sa.CheckConstraint(
            "raw_price_eur > 0",
            name="ck_valuation_listing_review_raw_price_positive",
        ),
        sa.CheckConstraint(
            "reviewed_at < expires_at",
            name="ck_valuation_listing_review_window",
        ),
    )
    op.create_index(
        "ix_valuation_listing_review_obs_active",
        "valuation_listing_review",
        ["obs_id", "is_active"],
    )
    op.create_index(
        "ix_valuation_listing_review_reviewed",
        "valuation_listing_review",
        ["reviewed_at"],
    )
    op.create_index(
        "ix_valuation_listing_review_expiry",
        "valuation_listing_review",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_valuation_listing_review_expiry", table_name="valuation_listing_review")
    op.drop_index("ix_valuation_listing_review_reviewed", table_name="valuation_listing_review")
    op.drop_index("ix_valuation_listing_review_obs_active", table_name="valuation_listing_review")
    op.drop_table("valuation_listing_review")
    op.drop_index("ix_valuation_reference_reviewed", table_name="verified_valuation_reference")
    op.drop_index("ix_valuation_reference_expiry", table_name="verified_valuation_reference")
    op.drop_index(
        "ix_valuation_reference_product_source_active",
        table_name="verified_valuation_reference",
    )
    op.drop_table("verified_valuation_reference")

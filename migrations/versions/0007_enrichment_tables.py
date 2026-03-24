"""Create listing_detail, listing_enrichment, listing_score tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0007_enrichment_tables"
down_revision = "0006_connector_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_detail",
        sa.Column("detail_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "obs_id",
            sa.BigInteger,
            sa.ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("description_length", sa.Integer, nullable=True),
        sa.Column("photo_urls", ARRAY(sa.Text), nullable=True),
        sa.Column("photo_count", sa.Integer, nullable=True),
        sa.Column("local_pickup_only", sa.Boolean, nullable=True),
        sa.Column("negotiation_enabled", sa.Boolean, nullable=True),
        sa.Column("original_posted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("seller_account_age_days", sa.Integer, nullable=True),
        sa.Column("seller_transaction_count", sa.Integer, nullable=True),
        sa.Column("view_count", sa.Integer, nullable=True),
        sa.Column("favorite_count", sa.Integer, nullable=True),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_listing_detail_fetched_at", "listing_detail", ["fetched_at"])

    op.create_table(
        "listing_enrichment",
        sa.Column("enrichment_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "obs_id",
            sa.BigInteger,
            sa.ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("urgency_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("urgency_keywords", ARRAY(sa.Text), nullable=True),
        sa.Column("has_original_box", sa.Boolean, nullable=True),
        sa.Column("has_receipt_or_invoice", sa.Boolean, nullable=True),
        sa.Column("accessories_included", ARRAY(sa.Text), nullable=True),
        sa.Column("accessories_completeness", sa.Numeric(3, 2), nullable=True),
        sa.Column("photo_quality_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("listing_quality_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("condition_confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("fakeness_probability", sa.Numeric(3, 2), nullable=True),
        sa.Column("seller_motivation_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("llm_model", sa.Text, nullable=True),
        sa.Column("llm_raw_response", JSONB, nullable=True),
        sa.Column(
            "enriched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("cost_tokens", sa.Integer, nullable=True),
    )
    op.create_index("ix_listing_enrichment_enriched_at", "listing_enrichment", ["enriched_at"])

    op.create_table(
        "listing_score",
        sa.Column("score_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "obs_id",
            sa.BigInteger,
            sa.ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product_template.product_id"),
            nullable=False,
        ),
        sa.Column("arbitrage_spread_eur", sa.Numeric, nullable=True),
        sa.Column("net_roi_pct", sa.Numeric, nullable=True),
        sa.Column("risk_adjusted_confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("acquisition_cost_eur", sa.Numeric, nullable=True),
        sa.Column("estimated_sale_price_eur", sa.Numeric, nullable=True),
        sa.Column("estimated_sell_fees_eur", sa.Numeric, nullable=True),
        sa.Column("estimated_sell_shipping_eur", sa.Numeric, nullable=True),
        sa.Column("days_on_market", sa.Integer, nullable=True),
        sa.Column("score_breakdown", JSONB, nullable=True),
        sa.Column(
            "scored_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_listing_score_product_confidence",
        "listing_score",
        ["product_id", sa.text("risk_adjusted_confidence DESC")],
    )
    op.create_index(
        "ix_listing_score_product_spread",
        "listing_score",
        ["product_id", sa.text("arbitrage_spread_eur DESC")],
    )


def downgrade() -> None:
    op.drop_table("listing_score")
    op.drop_table("listing_enrichment")
    op.drop_table("listing_detail")

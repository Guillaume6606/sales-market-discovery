"""Add sold-comparable valuation proposals awaiting operator review."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0014_sold_comp_proposals"
down_revision = "0013_seller_signal_semantics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sold_comp_proposal",
        sa.Column(
            "proposal_id",
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
        sa.Column("source", sa.Text, nullable=False, server_default="ebay_fr_sold_search"),
        sa.Column("condition", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("search_query", sa.Text, nullable=False),
        sa.Column("search_url", sa.Text, nullable=False),
        sa.Column("required_tokens", ARRAY(sa.Text), nullable=False),
        sa.Column("excluded_tokens", ARRAY(sa.Text), nullable=False),
        sa.Column("sale_count", sa.Integer, nullable=False),
        sa.Column("min_comparables", sa.Integer, nullable=False),
        sa.Column("median_price_eur", sa.Numeric(12, 2)),
        sa.Column("p25_price_eur", sa.Numeric(12, 2)),
        sa.Column("p75_price_eur", sa.Numeric(12, 2)),
        sa.Column("first_sold_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_sold_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sales", JSONB, nullable=False),
        sa.Column("excluded_sales", JSONB, nullable=False),
        sa.Column("reviewed_by", sa.Text),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("review_note", sa.Text),
        sa.Column(
            "reference_id",
            UUID,
            sa.ForeignKey("verified_valuation_reference.reference_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'insufficient')",
            name="ck_sold_comp_proposal_status",
        ),
        sa.CheckConstraint(
            "condition IN ('new', 'like_new', 'good', 'fair')",
            name="ck_sold_comp_proposal_condition",
        ),
        sa.CheckConstraint("sale_count >= 0", name="ck_sold_comp_proposal_sale_count"),
        sa.CheckConstraint("min_comparables >= 1", name="ck_sold_comp_proposal_min_comparables"),
        sa.CheckConstraint(
            "status <> 'pending' OR sale_count >= min_comparables",
            name="ck_sold_comp_proposal_pending_has_evidence",
        ),
        sa.CheckConstraint(
            "last_sold_at IS NULL OR last_sold_at <= computed_at",
            name="ck_sold_comp_proposal_evidence_before_decision",
        ),
    )
    op.create_index(
        "ix_sold_comp_proposal_product_status", "sold_comp_proposal", ["product_id", "status"]
    )
    op.create_index("ix_sold_comp_proposal_computed", "sold_comp_proposal", ["computed_at"])


def downgrade() -> None:
    op.drop_index("ix_sold_comp_proposal_computed", table_name="sold_comp_proposal")
    op.drop_index("ix_sold_comp_proposal_product_status", table_name="sold_comp_proposal")
    op.drop_table("sold_comp_proposal")

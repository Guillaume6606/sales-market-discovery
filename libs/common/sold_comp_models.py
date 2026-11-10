"""Sold-comparable valuation proposals.

A proposal is pre-filled evidence, not a valuation reference: the alert gate reads only
``verified_valuation_reference``. Approval by an operator creates that reference.
"""

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    UUID,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from libs.common.models import Base

PROPOSAL_STATUSES = ("pending", "approved", "rejected", "insufficient")


class SoldCompProposal(Base):
    __tablename__ = "sold_comp_proposal"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'insufficient')",
            name="ck_sold_comp_proposal_status",
        ),
        CheckConstraint(
            "condition IN ('new', 'like_new', 'good', 'fair')",
            name="ck_sold_comp_proposal_condition",
        ),
        CheckConstraint("sale_count >= 0", name="ck_sold_comp_proposal_sale_count"),
        CheckConstraint("min_comparables >= 1", name="ck_sold_comp_proposal_min_comparables"),
        CheckConstraint(
            "status <> 'pending' OR sale_count >= min_comparables",
            name="ck_sold_comp_proposal_pending_has_evidence",
        ),
        CheckConstraint(
            "last_sold_at IS NULL OR last_sold_at <= computed_at",
            name="ck_sold_comp_proposal_evidence_before_decision",
        ),
        Index("ix_sold_comp_proposal_product_status", "product_id", "status"),
        Index("ix_sold_comp_proposal_computed", "computed_at"),
    )

    proposal_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    product_id = Column(
        UUID,
        ForeignKey("product_template.product_id", ondelete="CASCADE"),
        nullable=False,
    )
    source = Column(Text, nullable=False, server_default="ebay_fr_sold_search")
    condition = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    search_query = Column(Text, nullable=False)
    search_url = Column(Text, nullable=False)
    required_tokens = Column(ARRAY(Text), nullable=False)
    excluded_tokens = Column(ARRAY(Text), nullable=False)
    sale_count = Column(Integer, nullable=False)
    min_comparables = Column(Integer, nullable=False)
    median_price_eur = Column(Numeric(12, 2))
    p25_price_eur = Column(Numeric(12, 2))
    p75_price_eur = Column(Numeric(12, 2))
    first_sold_at = Column(TIMESTAMP(timezone=True))
    last_sold_at = Column(TIMESTAMP(timezone=True))
    computed_at = Column(TIMESTAMP(timezone=True), nullable=False)
    sales = Column(JSONB, nullable=False)
    excluded_sales = Column(JSONB, nullable=False)
    reviewed_by = Column(Text)
    reviewed_at = Column(TIMESTAMP(timezone=True))
    review_note = Column(Text)
    reference_id = Column(
        UUID,
        ForeignKey("verified_valuation_reference.reference_id", ondelete="SET NULL"),
    )
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    product = relationship("ProductTemplate")

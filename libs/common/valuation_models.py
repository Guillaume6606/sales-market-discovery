from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Numeric,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from libs.common.models import Base


class VerifiedValuationReference(Base):
    __tablename__ = "verified_valuation_reference"
    __table_args__ = (
        CheckConstraint("currency = 'EUR'", name="ck_valuation_reference_currency_eur"),
        CheckConstraint(
            "condition IN ('new', 'like_new', 'good', 'fair')",
            name="ck_valuation_reference_condition",
        ),
        CheckConstraint("reviewed_price_eur > 0", name="ck_valuation_reference_price_positive"),
        CheckConstraint(
            "purchase_fee_rate >= 0 AND purchase_fee_rate <= 1",
            name="ck_valuation_reference_purchase_rate",
        ),
        CheckConstraint(
            "sell_fee_rate >= 0 AND sell_fee_rate <= 1",
            name="ck_valuation_reference_sell_rate",
        ),
        CheckConstraint(
            "social_charge_rate >= 0 AND social_charge_rate <= 1",
            name="ck_valuation_reference_social_rate",
        ),
        CheckConstraint(
            "purchase_fee_fixed_eur >= 0 AND sell_fee_fixed_eur >= 0 "
            "AND outbound_logistics_eur >= 0 AND risk_allowance_eur >= 0 "
            "AND minimum_contribution_eur >= 0",
            name="ck_valuation_reference_costs_nonnegative",
        ),
        CheckConstraint(
            "comparable_sold_at <= reviewed_at AND reviewed_at < expires_at",
            name="ck_valuation_reference_evidence_window",
        ),
        Index(
            "ix_valuation_reference_product_source_active",
            "product_id",
            "purchase_source",
            "is_active",
        ),
        Index("ix_valuation_reference_expiry", "expires_at"),
        Index("ix_valuation_reference_reviewed", "reviewed_at"),
    )

    reference_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    product_id = Column(
        UUID,
        ForeignKey("product_template.product_id", ondelete="CASCADE"),
        nullable=False,
    )
    purchase_source = Column(Text, nullable=False)
    destination_marketplace = Column(Text, nullable=False)
    required_tokens = Column(ARRAY(Text), nullable=False)
    excluded_tokens = Column(ARRAY(Text), nullable=False)
    condition = Column(Text, nullable=False)
    reviewed_price_eur = Column(Numeric(12, 2), nullable=False)
    currency = Column(Text, nullable=False)
    evidence_url = Column(Text, nullable=False)
    comparable_sold_at = Column(TIMESTAMP(timezone=True), nullable=False)
    reviewed_at = Column(TIMESTAMP(timezone=True), nullable=False)
    reviewed_by = Column(Text, nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    limitations = Column(Text, nullable=False)
    purchase_fee_rate = Column(Numeric(8, 6), nullable=False)
    purchase_fee_fixed_eur = Column(Numeric(12, 2), nullable=False)
    sell_fee_rate = Column(Numeric(8, 6), nullable=False)
    sell_fee_fixed_eur = Column(Numeric(12, 2), nullable=False)
    social_charge_rate = Column(Numeric(8, 6), nullable=False)
    outbound_logistics_eur = Column(Numeric(12, 2), nullable=False)
    risk_allowance_eur = Column(Numeric(12, 2), nullable=False)
    minimum_contribution_eur = Column(Numeric(12, 2), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    product = relationship("ProductTemplate")


class ValuationListingReview(Base):
    __tablename__ = "valuation_listing_review"
    __table_args__ = (
        CheckConstraint(
            "reviewed_condition IN ('new', 'like_new', 'good', 'fair')",
            name="ck_valuation_listing_review_condition",
        ),
        CheckConstraint(
            "reviewed_shipping_cost_eur >= 0",
            name="ck_valuation_listing_review_shipping_nonnegative",
        ),
        CheckConstraint(
            "raw_price_eur > 0",
            name="ck_valuation_listing_review_raw_price_positive",
        ),
        CheckConstraint(
            "reviewed_at < expires_at",
            name="ck_valuation_listing_review_window",
        ),
        Index("ix_valuation_listing_review_obs_active", "obs_id", "is_active"),
        Index("ix_valuation_listing_review_reviewed", "reviewed_at"),
        Index("ix_valuation_listing_review_expiry", "expires_at"),
    )

    review_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    obs_id = Column(
        BigInteger,
        ForeignKey("listing_observation.obs_id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewed_title = Column(Text, nullable=False)
    reviewed_condition = Column(Text, nullable=False)
    reviewed_delivery_to_france = Column(Boolean)
    delivery_evidence = Column(Text)
    raw_delivery_to_france = Column(Boolean)
    raw_delivery_evidence = Column(Text)
    raw_url = Column(Text)
    reviewed_shipping_cost_eur = Column(Numeric(12, 2), nullable=False)
    reviewed_by = Column(Text, nullable=False)
    reviewed_at = Column(TIMESTAMP(timezone=True), nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    notes = Column(Text, nullable=False)
    raw_title = Column(Text, nullable=False)
    raw_price_eur = Column(Numeric(12, 2), nullable=False)
    raw_condition = Column(Text)
    raw_shipping_cost_eur = Column(Numeric(12, 2))
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    observation = relationship("ListingObservation")

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import (
    ARRAY,
    JSON,
    TIMESTAMP,
    UUID,
    BigInteger,
    Boolean,
    Column,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Category(Base):
    __tablename__ = "category"

    category_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    name = Column(Text, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductTemplate(Base):
    __tablename__ = "product_template"

    product_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    name = Column(Text, nullable=False)
    description = Column(Text)
    search_query = Column(Text, nullable=False)
    category_id = Column(UUID, ForeignKey("category.category_id"), nullable=False)
    brand = Column(Text)
    price_min = Column(Numeric)
    price_max = Column(Numeric)
    providers = Column(ARRAY(Text), default=list)
    words_to_avoid = Column(ARRAY(Text), default=list)
    enable_llm_validation = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())
    last_ingested_at = Column(TIMESTAMP(timezone=True))

    category = relationship("Category", back_populates="products")


class ListingObservation(Base):
    __tablename__ = "listing_observation"
    __table_args__ = (
        UniqueConstraint("source", "listing_id", "product_id", name="uq_listing_source_product"),
    )

    obs_id = Column(BigInteger, primary_key=True, autoincrement=True)
    product_id = Column(UUID, ForeignKey("product_template.product_id"))
    source = Column(Text)
    listing_id = Column(Text)
    title = Column(Text)
    price = Column(Numeric)
    currency = Column(Text)
    condition = Column(Text)
    is_sold = Column(Boolean)
    seller_rating = Column(Numeric)
    shipping_cost = Column(Numeric)
    location = Column(Text)
    observed_at = Column(TIMESTAMP(timezone=True))
    url = Column(Text)  # Listing URL for direct access
    last_seen_at = Column(TIMESTAMP(timezone=True))
    is_stale = Column(Boolean, server_default="false", default=False)
    llm_validated = Column(Boolean, default=False)
    llm_validation_result = Column(JSON)
    llm_validated_at = Column(TIMESTAMP(timezone=True))
    screenshot_path = Column(Text)

    product = relationship("ProductTemplate", back_populates="observations")


class ProductDailyMetrics(Base):
    __tablename__ = "product_daily_metrics"

    product_id = Column(UUID, ForeignKey("product_template.product_id"), primary_key=True)
    date = Column(Date, primary_key=True)
    sold_count_7d = Column(Integer)
    sold_count_30d = Column(Integer)
    price_median = Column(Numeric)
    price_std = Column(Numeric)
    price_p25 = Column(Numeric)
    price_p75 = Column(Numeric)
    liquidity_score = Column(Numeric)
    trend_score = Column(Numeric)

    product = relationship("ProductTemplate", back_populates="daily_metrics")


class MarketPriceNormal(Base):
    __tablename__ = "market_price_normal"

    product_id = Column(UUID, ForeignKey("product_template.product_id"), primary_key=True)
    last_computed_at = Column(TIMESTAMP(timezone=True))
    pmn = Column(Numeric)
    pmn_low = Column(Numeric)
    pmn_high = Column(Numeric)
    methodology = Column(JSON)

    # Relationship
    product = relationship("ProductTemplate", back_populates="pmn")


class AlertRule(Base):
    __tablename__ = "alert_rule"

    rule_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    name = Column(Text)
    product_filter = Column(JSON)
    threshold_pct = Column(Numeric)
    min_margin_abs = Column(Numeric)
    min_liquidity_score = Column(Numeric)
    min_seller_rating = Column(Numeric)
    channels = Column(ARRAY(Text))
    is_active = Column(Boolean, default=True, server_default="true")


class AlertEvent(Base):
    __tablename__ = "alert_event"

    alert_id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_id = Column(UUID, ForeignKey("alert_rule.rule_id"))
    product_id = Column(UUID, ForeignKey("product_template.product_id"))
    obs_id = Column(BigInteger, ForeignKey("listing_observation.obs_id"))
    sent_at = Column(TIMESTAMP(timezone=True))
    delivery = Column(JSON)
    suppressed = Column(Boolean)

    # Relationships
    rule = relationship("AlertRule")
    product = relationship("ProductTemplate")
    observation = relationship("ListingObservation")


class IngestionRun(Base):
    __tablename__ = "ingestion_run"

    run_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    product_id = Column(UUID, ForeignKey("product_template.product_id"))
    source = Column(Text)
    function_name = Column(Text)
    status = Column(Text)  # running/success/error/no_data
    started_at = Column(TIMESTAMP(timezone=True))
    finished_at = Column(TIMESTAMP(timezone=True))
    duration_s = Column(Numeric)
    listings_fetched = Column(Integer)
    listings_deduped = Column(Integer)
    listings_persisted = Column(Integer)
    filtering_stats = Column(JSON)
    error_message = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    product = relationship("ProductTemplate")


class AlertFeedback(Base):
    __tablename__ = "alert_feedback"

    feedback_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    alert_id = Column(BigInteger, ForeignKey("alert_event.alert_id"))
    feedback = Column(Text)  # interested/not_interested/purchased
    notes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    alert = relationship("AlertEvent", back_populates="feedbacks")


AlertEvent.feedbacks = relationship("AlertFeedback", back_populates="alert")

Category.products = relationship("ProductTemplate", back_populates="category")
ProductTemplate.observations = relationship("ListingObservation", back_populates="product")
ProductTemplate.daily_metrics = relationship("ProductDailyMetrics", back_populates="product")
ProductTemplate.pmn = relationship("MarketPriceNormal", back_populates="product", uselist=False)


# Standardized Listing model for all connectors
class Listing(BaseModel):
    source: Literal["ebay", "leboncoin", "vinted"]
    listing_id: str
    title: str
    price: float | None
    currency: str
    condition_raw: str | None
    condition_norm: Literal["new", "like_new", "good", "fair"] | None
    location: str | None
    seller_rating: float | None
    shipping_cost: float | None
    observed_at: datetime  # sold_at or seen_at
    is_sold: bool
    url: str | None
    brand: str | None = None
    size: str | None = None
    color: str | None = None

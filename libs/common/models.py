from sqlalchemy import Column, Integer, String, Text, Boolean, Numeric, TIMESTAMP, UUID, BigInteger, Date, JSON, ARRAY, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from pydantic import BaseModel
from typing import Literal
from datetime import datetime

Base = declarative_base()

class Category(Base):
    __tablename__ = "category"

    category_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    name = Column(Text, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_ingested_at = Column(TIMESTAMP(timezone=True))

    category = relationship("Category", back_populates="products")

class ListingObservation(Base):
    __tablename__ = "listing_observation"

    obs_id = Column(BigInteger, primary_key=True, autoincrement=True)
    product_id = Column(UUID, ForeignKey("product_template.product_id"))
    source = Column(Text)
    listing_id = Column(Text)
    title = Column(Text)
    description = Column(Text)  # Product description
    price = Column(Numeric)
    currency = Column(Text)
    condition = Column(Text)
    is_sold = Column(Boolean)
    seller_rating = Column(Numeric)
    shipping_cost = Column(Numeric)
    location = Column(Text)
    observed_at = Column(TIMESTAMP(timezone=True))
    url = Column(Text)  # Listing URL for direct access

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

Category.products = relationship("ProductTemplate", back_populates="category")
ProductTemplate.observations = relationship("ListingObservation", back_populates="product")
ProductTemplate.daily_metrics = relationship("ProductDailyMetrics", back_populates="product")
ProductTemplate.pmn = relationship("MarketPriceNormal", back_populates="product", uselist=False)

# Standardized Listing model for all connectors
class Listing(BaseModel):
    source: Literal["ebay", "leboncoin", "vinted", "fnac", "cdiscount", "backmarket", "rakuten"]
    listing_id: str
    title: str
    description: str | None = None  # Product description
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

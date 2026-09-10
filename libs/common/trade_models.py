"""Trade ledger models.

``settled`` means payout and the applicable return window are final. ``returned`` means
the position was returned to its supplier and closed; a customer-returned item intended
for resale stays open and must not be marked settled or returned.
"""

from sqlalchemy import (
    TIMESTAMP,
    UUID,
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy.sql import func

from libs.common.models import Base

TRADE_STATUSES = (
    "purchased",
    "listed",
    "sold",
    "settled",
    "returned",
    "written_off",
)
OPEN_TRADE_STATUSES = ("purchased", "listed", "sold")
TERMINAL_TRADE_STATUSES = ("settled", "returned", "written_off")


class Trade(Base):
    __tablename__ = "trade"
    __table_args__ = (
        CheckConstraint(
            "status IN ('purchased', 'listed', 'sold', 'settled', 'returned', 'written_off')",
            name="ck_trade_status",
        ),
        CheckConstraint("acquisition_price_eur >= 0", name="ck_trade_acquisition_nonnegative"),
        CheckConstraint("acquisition_fees_eur >= 0", name="ck_trade_acquisition_fees_nonnegative"),
        CheckConstraint(
            "inbound_logistics_eur >= 0", name="ck_trade_inbound_logistics_nonnegative"
        ),
        CheckConstraint("repair_cost_eur >= 0", name="ck_trade_repairs_nonnegative"),
        CheckConstraint("selling_fees_eur >= 0", name="ck_trade_selling_fees_nonnegative"),
        CheckConstraint(
            "outbound_logistics_eur >= 0", name="ck_trade_outbound_logistics_nonnegative"
        ),
        CheckConstraint("refund_cost_eur >= 0", name="ck_trade_refunds_nonnegative"),
        CheckConstraint("turnover_charges_eur >= 0", name="ck_trade_turnover_nonnegative"),
        CheckConstraint("sale_revenue_eur >= 0", name="ck_trade_revenue_nonnegative"),
        CheckConstraint(
            "forecast_sale_revenue_eur >= 0",
            name="ck_trade_forecast_revenue_nonnegative",
        ),
        CheckConstraint(
            "forecast_remaining_costs_eur >= 0",
            name="ck_trade_forecast_costs_nonnegative",
        ),
        CheckConstraint("time_spent_minutes >= 0", name="ck_trade_time_nonnegative"),
        CheckConstraint("sold_on IS NULL OR sold_on >= acquired_on", name="ck_trade_sold_date"),
        CheckConstraint(
            "settled_on IS NULL OR (sold_on IS NOT NULL AND settled_on >= sold_on)",
            name="ck_trade_settled_date",
        ),
        CheckConstraint(
            "closed_on IS NULL OR (closed_on >= acquired_on AND "
            "(sold_on IS NULL OR closed_on >= sold_on))",
            name="ck_trade_closed_date",
        ),
    )

    trade_id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    observation_id = Column(
        BigInteger,
        ForeignKey("listing_observation.obs_id", ondelete="SET NULL"),
        nullable=True,
    )
    title = Column(Text, nullable=False)
    buy_platform = Column(Text, nullable=False)
    exit_platform = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="purchased", server_default="purchased")
    acquired_on = Column(Date, nullable=False)
    sold_on = Column(Date)
    settled_on = Column(Date)
    closed_on = Column(Date)

    acquisition_price_eur = Column(Numeric(12, 2), nullable=False)
    acquisition_fees_eur = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    inbound_logistics_eur = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    repair_cost_eur = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    selling_fees_eur = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    outbound_logistics_eur = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    refund_cost_eur = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    turnover_charges_eur = Column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    sale_revenue_eur = Column(Numeric(12, 2))

    forecast_sale_revenue_eur = Column(Numeric(12, 2))
    forecast_remaining_costs_eur = Column(Numeric(12, 2))

    time_spent_minutes = Column(Integer, nullable=False, default=0, server_default="0")
    notes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MonthlyOverhead(Base):
    __tablename__ = "monthly_overhead"
    __table_args__ = (CheckConstraint("amount_eur >= 0", name="ck_monthly_overhead_nonnegative"),)

    month = Column(Date, primary_key=True)
    amount_eur = Column(Numeric(12, 2), nullable=False)
    notes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

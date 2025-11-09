from alembic import op
import sqlalchemy as sa

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.create_table(
        "product_ref",
        sa.Column("product_id", sa.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("canonical_title", sa.Text),
        sa.Column("brand", sa.Text),
        sa.Column("gtin", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "listing_observation",
        sa.Column("obs_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("product_id", sa.UUID, sa.ForeignKey("product_ref.product_id")),
        sa.Column("source", sa.Text),
        sa.Column("listing_id", sa.Text),
        sa.Column("title", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("price", sa.Numeric),
        sa.Column("currency", sa.Text),
        sa.Column("condition", sa.Text),
        sa.Column("is_sold", sa.Boolean),
        sa.Column("seller_rating", sa.Numeric),
        sa.Column("shipping_cost", sa.Numeric),
        sa.Column("location", sa.Text),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True)),
    )

    op.create_table(
        "product_daily_metrics",
        sa.Column("product_id", sa.UUID, sa.ForeignKey("product_ref.product_id"), primary_key=True),
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("sold_count_7d", sa.Integer),
        sa.Column("sold_count_30d", sa.Integer),
        sa.Column("price_median", sa.Numeric),
        sa.Column("price_std", sa.Numeric),
        sa.Column("price_p25", sa.Numeric),
        sa.Column("price_p75", sa.Numeric),
        sa.Column("liquidity_score", sa.Numeric),
        sa.Column("trend_score", sa.Numeric),
    )

    op.create_table(
        "market_price_normal",
        sa.Column("product_id", sa.UUID, sa.ForeignKey("product_ref.product_id"), primary_key=True),
        sa.Column("last_computed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("pmn", sa.Numeric),
        sa.Column("pmn_low", sa.Numeric),
        sa.Column("pmn_high", sa.Numeric),
        sa.Column("methodology", sa.JSON),
    )

    op.create_table(
        "alert_rule",
        sa.Column("rule_id", sa.UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text),
        sa.Column("product_filter", sa.JSON),
        sa.Column("threshold_pct", sa.Numeric),
        sa.Column("min_margin_abs", sa.Numeric),
        sa.Column("min_liquidity_score", sa.Numeric),
        sa.Column("min_seller_rating", sa.Numeric),
        sa.Column("channels", sa.ARRAY(sa.Text)),
    )

    op.create_table(
        "alert_event",
        sa.Column("alert_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("rule_id", sa.UUID, sa.ForeignKey("alert_rule.rule_id")),
        sa.Column("product_id", sa.UUID, sa.ForeignKey("product_ref.product_id")),
        sa.Column("obs_id", sa.BigInteger, sa.ForeignKey("listing_observation.obs_id")),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("delivery", sa.JSON),
        sa.Column("suppressed", sa.Boolean, server_default=sa.text("false")),
    )

def downgrade():
    op.drop_table("alert_event")
    op.drop_table("alert_rule")
    op.drop_table("market_price_normal")
    op.drop_table("product_daily_metrics")
    op.drop_table("listing_observation")
    op.drop_table("product_ref")

import sqlalchemy as sa
from alembic import op

revision = "0005_week4_profit_missing_data"
down_revision = "0004_week3_pmn_history"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("alert_feedback", sa.Column("profit", sa.Numeric, nullable=True))
    op.add_column("ingestion_run", sa.Column("listings_missing_price", sa.Integer, nullable=True))
    op.add_column("ingestion_run", sa.Column("listings_missing_title", sa.Integer, nullable=True))


def downgrade():
    op.drop_column("ingestion_run", "listings_missing_title")
    op.drop_column("ingestion_run", "listings_missing_price")
    op.drop_column("alert_feedback", "profit")

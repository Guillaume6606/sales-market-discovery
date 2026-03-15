import sqlalchemy as sa
from alembic import op

revision = "0003_week2_confidence"
down_revision = "0002_week1_foundations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("market_price_normal", sa.Column("confidence", sa.Numeric))


def downgrade():
    op.drop_column("market_price_normal", "confidence")

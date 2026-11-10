"""Add explicit identity tokens to product templates."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0015_product_identity_tokens"
down_revision = "0014_sold_comp_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("required_tokens", "excluded_tokens"):
        op.add_column(
            "product_template",
            sa.Column(column, ARRAY(sa.Text), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    for column in ("excluded_tokens", "required_tokens"):
        op.drop_column("product_template", column)

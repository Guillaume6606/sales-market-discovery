"""Add description column to listing_observation

Revision ID: 0002_add_description
Revises: 0001_init
Create Date: 2025-11-02

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_add_description"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade():
    """Add description and url columns to listing_observation table"""
    # Add description column (nullable to allow existing rows)
    op.add_column(
        "listing_observation",
        sa.Column("description", sa.Text(), nullable=True)
    )
    
    # Add url column if it doesn't exist (for listing URLs)
    # Check if column exists first to make migration idempotent
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns('listing_observation')]
    
    if 'url' not in columns:
        op.add_column(
            "listing_observation",
            sa.Column("url", sa.Text(), nullable=True)
        )


def downgrade():
    """Remove description and url columns from listing_observation table"""
    op.drop_column("listing_observation", "description")
    
    # Only drop url if it exists
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns('listing_observation')]
    
    if 'url' in columns:
        op.drop_column("listing_observation", "url")



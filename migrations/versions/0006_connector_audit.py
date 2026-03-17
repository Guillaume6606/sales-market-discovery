"""connector_audit table"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_audit",
        sa.Column("audit_id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ingestion_run_id", UUID, nullable=True),
        sa.Column("obs_id", sa.BigInteger, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("audit_mode", sa.Text, nullable=False),
        sa.Column("screenshot_path", sa.Text, nullable=True),
        sa.Column("html_snippet", sa.Text, nullable=True),
        sa.Column("llm_response", JSONB, nullable=True),
        sa.Column("field_results", JSONB, nullable=True),
        sa.Column("accuracy_score", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "audited_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("cost_tokens", sa.Integer, nullable=True),
    )
    op.create_foreign_key(
        "fk_connector_audit_run",
        "connector_audit",
        "ingestion_run",
        ["ingestion_run_id"],
        ["run_id"],
    )
    op.create_foreign_key(
        "fk_connector_audit_obs", "connector_audit", "listing_observation", ["obs_id"], ["obs_id"]
    )
    op.create_check_constraint(
        "ck_connector_audit_mode",
        "connector_audit",
        "audit_mode IN ('continuous', 'on_demand', 'cli')",
    )
    op.create_index("ix_connector_audit_source_date", "connector_audit", ["source", "audited_at"])
    op.create_index("ix_connector_audit_obs", "connector_audit", ["obs_id"])


def downgrade() -> None:
    op.drop_index("ix_connector_audit_obs")
    op.drop_index("ix_connector_audit_source_date")
    op.drop_constraint("ck_connector_audit_mode", "connector_audit", type_="check")
    op.drop_constraint("fk_connector_audit_obs", "connector_audit", type_="foreignkey")
    op.drop_constraint("fk_connector_audit_run", "connector_audit", type_="foreignkey")
    op.drop_table("connector_audit")

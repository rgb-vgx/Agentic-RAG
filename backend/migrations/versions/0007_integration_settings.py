"""per-user integration credentials

Revision ID: 0007_integration_settings
Revises: 0006_document_domains
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_integration_settings"
down_revision = "0006_document_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("encrypted_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "provider",
            name="uq_integration_tenant_user_provider",
        ),
    )
    op.create_index(
        "ix_integration_settings_tenant_user",
        "integration_settings",
        ["tenant_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_settings_tenant_user", table_name="integration_settings"
    )
    op.drop_table("integration_settings")

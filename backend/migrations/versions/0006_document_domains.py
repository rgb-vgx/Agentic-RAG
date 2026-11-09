"""document domains: taxonomy persistence and per-document selection

Revision ID: 0006_document_domains
Revises: 0005_conversation_title_source
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_document_domains"
down_revision = "0005_conversation_title_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("label", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "hints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "slug", name="uq_document_domains_tenant_slug"
        ),
    )
    op.create_index(
        "ix_document_domains_tenant_id", "document_domains", ["tenant_id"]
    )

    op.add_column(
        "documents",
        sa.Column(
            "domain", sa.String(64), nullable=False, server_default="general"
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "domain_source",
            sa.String(16),
            nullable=False,
            server_default="fallback",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("domain_confidence", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "domain_confidence")
    op.drop_column("documents", "domain_source")
    op.drop_column("documents", "domain")

    op.drop_index("ix_document_domains_tenant_id", table_name="document_domains")
    op.drop_table("document_domains")

"""legal document metadata on documents

Revision ID: 0004_document_legal_metadata
Revises: 0003_auth_tenancy
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_document_legal_metadata"
down_revision = "0003_auth_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("title", sa.String(512), nullable=True))
    op.add_column(
        "documents",
        sa.Column("title_normalized", sa.String(512), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("document_number", sa.String(64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("document_year", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_documents_title_normalized",
        "documents",
        ["title_normalized"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_title_normalized", table_name="documents")
    op.drop_column("documents", "document_year")
    op.drop_column("documents", "document_number")
    op.drop_column("documents", "title_normalized")
    op.drop_column("documents", "title")

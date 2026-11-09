"""conversation title provenance

Revision ID: 0005_conversation_title_source
Revises: 0004_document_legal_metadata
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_conversation_title_source"
down_revision = "0004_document_legal_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "title_source",
            sa.String(16),
            nullable=False,
            server_default="first_message",
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "title_source")

"""active chunk set pointer on documents

Revision ID: 0002_active_chunk_set
Revises: 0001_initial
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_active_chunk_set"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("active_chunk_set_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "active_chunk_set_id")

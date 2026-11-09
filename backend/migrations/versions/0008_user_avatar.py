"""store Google profile picture on users

Revision ID: 0008_user_avatar
Revises: 0007_integration_settings
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_user_avatar"
down_revision = "0007_integration_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_url")

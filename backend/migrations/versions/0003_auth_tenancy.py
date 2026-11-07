"""auth tenancy: tenants/users/memberships + visibility columns

Revision ID: 0003_auth_tenancy
Revises: 0002_active_chunk_set
Create Date: 2026-11-07

Ported from the reference schema db/0001_tenancy_visibility.sql of the
rag_auth package, adapted to this project:

- tenant_id stays String(64) (project-wide convention) instead of UUID.
- documents.user_id keeps its name and semantics = owner (no rename).
- Composite FKs (owner, tenant) -> memberships and
  (conversation, tenant, user) -> conversations make the DB itself reject
  rows whose owner/conversation belongs to a different tenant, even when
  application code has a bug.
- scope has NO DEFAULT: callers must state org|personal explicitly.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_auth_tenancy"
down_revision = "0002_active_chunk_set"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- identity -----------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("google_sub", sa.Text(), nullable=False, unique=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )

    # One user belongs to exactly ONE tenant (PK = user_id). Changing this
    # assumption means changing both the JWT claims and every repository.
    op.create_table(
        "memberships",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            sa.String(64),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "roles",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY['member']"),
        ),
        sa.UniqueConstraint("user_id", "tenant_id"),
    )

    # --- documents: scope + quarantine + composite owner FK ------------------------
    op.add_column("documents", sa.Column("scope", sa.String(16), nullable=True))
    op.execute("UPDATE documents SET scope = 'org'")  # legacy rows: shared
    op.alter_column("documents", "scope", nullable=False)
    op.create_check_constraint(
        "ck_documents_scope", "documents", "scope IN ('org', 'personal')"
    )
    op.add_column(
        "documents",
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    # owner must belong to the tenant that owns the document
    op.create_foreign_key(
        "fk_documents_owner_membership",
        "documents",
        "memberships",
        ["user_id", "tenant_id"],
        ["user_id", "tenant_id"],
    )
    op.create_index(
        "ix_documents_tenant_scope_user",
        "documents",
        ["tenant_id", "scope", "user_id"],
    )

    # --- conversations: composite target for message/run FKs -----------------------
    op.create_unique_constraint(
        "uq_conversations_id_tenant_user",
        "conversations",
        ["id", "tenant_id", "user_id"],
    )

    # --- messages: user_id + composite FK -----------------------------------------
    op.add_column("messages", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        "UPDATE messages m SET user_id = c.user_id "
        "FROM conversations c WHERE c.id = m.conversation_id"
    )
    op.alter_column("messages", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_messages_conversation_owner",
        "messages",
        "conversations",
        ["conversation_id", "tenant_id", "user_id"],
        ["id", "tenant_id", "user_id"],
    )

    # --- agent_runs: visibility snapshot + composite FK ---------------------------
    op.add_column(
        "agent_runs",
        sa.Column(
            "snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_foreign_key(
        "fk_agent_runs_conversation_owner",
        "agent_runs",
        "conversations",
        ["conversation_id", "tenant_id", "user_id"],
        ["id", "tenant_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_runs_conversation_owner", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "snapshot")

    op.drop_constraint("fk_messages_conversation_owner", "messages", type_="foreignkey")
    op.drop_column("messages", "user_id")

    op.drop_constraint("uq_conversations_id_tenant_user", "conversations", type_="unique")

    op.drop_index("ix_documents_tenant_scope_user", table_name="documents")
    op.drop_constraint("fk_documents_owner_membership", "documents", type_="foreignkey")
    op.drop_column("documents", "quarantined_at")
    op.drop_constraint("ck_documents_scope", "documents", type_="check")
    op.drop_column("documents", "scope")

    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("tenants")

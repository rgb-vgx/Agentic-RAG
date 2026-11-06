"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("knowledge_base_id", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("quality_report", postgresql.JSONB(), nullable=True),
        sa.Column("run_metadata", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("related_document_ids", postgresql.JSONB(), nullable=False,
                  server_default="[]"),
        sa.Column("references", postgresql.JSONB(), nullable=False,
                  server_default="[]"),
        sa.Column("supersedes", postgresql.JSONB(), nullable=False,
                  server_default="[]"),
        sa.Column("document_version", sa.String(32), nullable=True),
        sa.Column("effective_from", sa.DateTime(), nullable=True),
        sa.Column("effective_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_documents_tenant", "documents", ["tenant_id"])
    op.create_index("ix_documents_tenant_kb", "documents", ["tenant_id", "knowledge_base_id"])

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(256), nullable=False,
                  server_default="New conversation"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_conversations_tenant", "conversations", ["tenant_id"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("token_usage", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("workflow_version", sa.String(32), nullable=True),
        sa.Column("prompt_versions", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"],
                                ondelete="CASCADE"),
    )
    op.create_index("ix_messages_conversation", "messages",
                    ["conversation_id", "created_at"])

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("resolved_query", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("query_analysis", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("routing_path", sa.String(16), nullable=False,
                  server_default="standard"),
        sa.Column("retrieval_iterations", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("workflow_version", sa.String(32), nullable=False,
                  server_default="1.0"),
        sa.Column("model_provider", sa.String(32), nullable=False,
                  server_default="openai"),
        sa.Column("model_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("embedding_model", sa.String(128), nullable=False, server_default=""),
        sa.Column("retrieval_config_version", sa.String(32), nullable=False,
                  server_default="1.0"),
        sa.Column("prompt_versions", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("token_usage", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_runs_tenant", "agent_runs", ["tenant_id"])
    op.create_index("ix_agent_runs_conversation", "agent_runs",
                    ["conversation_id", "created_at"])

    op.create_table(
        "chunking_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_set_id", sa.String(64), nullable=False),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("strategy_config", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("strategy_version", sa.String(32), nullable=False,
                  server_default="1.0"),
        sa.Column("section_results", postgresql.JSONB(), nullable=False,
                  server_default="[]"),
        sa.Column("quality_scores", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "indexing_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_set_id", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column("sparse_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("points_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "evaluation_questions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset", sa.String(64), nullable=False, server_default="default"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_evidence", postgresql.JSONB(), nullable=False,
                  server_default="[]"),
        sa.Column("expected_answer", sa.Text(), nullable=True),
        sa.Column("section_id", sa.String(64), nullable=True),
        sa.Column("difficulty", sa.String(16), nullable=False,
                  server_default="medium"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    for table in (
        "evaluation_questions",
        "indexing_runs",
        "chunking_runs",
        "agent_runs",
        "messages",
        "conversations",
        "documents",
    ):
        op.drop_table(table)

"""SQLAlchemy declarative models (Section 9).

One model per proposal table. Versioning snapshot columns (9.7) are
included so every run can reproduce its results.
"""

import uuid
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )


class TenantMixin:
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


# ---------------------------------------------------------------------------
# Identity (tenancy + visibility, rag_auth port)
# ---------------------------------------------------------------------------


class TenantRecord(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    google_sub: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MembershipRecord(Base):
    """A user belongs to exactly ONE tenant (PK = user_id)."""

    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False
    )
    roles: Mapped[list] = mapped_column(
        ARRAY(Text), nullable=False, default=["member"]
    )

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id"),
    )


# ---------------------------------------------------------------------------
# Documents (Section 9.4)
# ---------------------------------------------------------------------------


class DocumentRecord(Base, TenantMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # user_id = owner of the document (column name kept for continuity)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Visibility: org = shared inside the tenant, personal = owner only.
    # No DEFAULT — callers must state the scope explicitly.
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # Legal identity, extracted during ingestion. Used to resolve exact
    # references such as "Điều 19 Bộ luật tố tụng dân sự" without relying
    # on semantic similarity.
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title_normalized: Mapped[str | None] = mapped_column(String(512), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(24), default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    quality_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    run_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Source of truth for the queryable chunk set (publish-after-verify)
    active_chunk_set_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Document relationship metadata (Section 9.6)
    related_document_ids: Mapped[list] = mapped_column(JSONB, default=list)
    references: Mapped[list] = mapped_column(JSONB, default=list)
    supersedes: Mapped[list] = mapped_column(JSONB, default=list)
    document_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_documents_tenant_kb", "tenant_id", "knowledge_base_id"),
        Index("ix_documents_tenant_scope_user", "tenant_id", "scope", "user_id"),
        CheckConstraint("scope IN ('org', 'personal')", name="ck_documents_scope"),
        # owner must be a member of the tenant that owns the document
        ForeignKeyConstraint(
            ["user_id", "tenant_id"],
            ["memberships.user_id", "memberships.tenant_id"],
            name="fk_documents_owner_membership",
        ),
    )


# ---------------------------------------------------------------------------
# Conversations (Section 9.2)
# ---------------------------------------------------------------------------


class ConversationRecord(Base, TenantMixin):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(256), default="New conversation")
    # first_message | auto | manual — manual titles are never overwritten.
    title_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="first_message"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["MessageRecord"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        # messages has both a single FK and a composite (conversation,
        # tenant, user) FK; pin the relationship to the single one.
        foreign_keys="MessageRecord.conversation_id",
    )

    __table_args__ = (
        # target of the composite FKs from messages/agent_runs
        UniqueConstraint("id", "tenant_id", "user_id"),
    )


class MessageRecord(Base, TenantMixin):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    # denormalized owner of the conversation; backfilled by migration 0003
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    sources: Mapped[list] = mapped_column(JSONB, default=list)
    token_usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Versioning snapshot (Section 9.7)
    workflow_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_versions: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    conversation: Mapped[ConversationRecord] = relationship(
        back_populates="messages",
        foreign_keys="MessageRecord.conversation_id",
    )

    __table_args__ = (
        Index("ix_messages_conversation", "conversation_id", "created_at"),
        # message must belong to the same conversation AND same user/tenant
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "user_id"],
            ["conversations.id", "conversations.tenant_id", "conversations.user_id"],
            name="fk_messages_conversation_owner",
        ),
    )


# ---------------------------------------------------------------------------
# Agent runs (Section 9.3)
# ---------------------------------------------------------------------------


class AgentRunRecord(Base, TenantMixin):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Retrieval visibility snapshot for this run: {document_id: chunk_set_id}.
    # Computed from the caller's visible documents; client-provided ids can
    # only NARROW it, never widen it.
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)

    query: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_query: Mapped[dict] = mapped_column(JSONB, default=dict)
    query_analysis: Mapped[dict] = mapped_column(JSONB, default=dict)

    routing_path: Mapped[str] = mapped_column(String(16), default="standard")
    retrieval_iterations: Mapped[int] = mapped_column(Integer, default=0)

    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list] = mapped_column(JSONB, default=list)

    # Versioning snapshot (Section 9.7)
    workflow_version: Mapped[str] = mapped_column(String(32), default="1.0")
    model_provider: Mapped[str] = mapped_column(String(32), default="openai")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    embedding_model: Mapped[str] = mapped_column(String(128), default="")
    retrieval_config_version: Mapped[str] = mapped_column(String(32), default="1.0")
    prompt_versions: Mapped[dict] = mapped_column(JSONB, default=dict)

    token_usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_agent_runs_conversation", "conversation_id", "created_at"),
        # run must belong to the same conversation AND same user/tenant
        ForeignKeyConstraint(
            ["conversation_id", "tenant_id", "user_id"],
            ["conversations.id", "conversations.tenant_id", "conversations.user_id"],
            name="fk_agent_runs_conversation_owner",
        ),
    )


# ---------------------------------------------------------------------------
# Versioned runs (Sections 9.5, 9.7)
# ---------------------------------------------------------------------------


class ChunkingRunRecord(Base, TenantMixin):
    __tablename__ = "chunking_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_set_id: Mapped[str] = mapped_column(String(64), nullable=False)

    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    strategy_version: Mapped[str] = mapped_column(String(32), default="1.0")

    section_results: Mapped[list] = mapped_column(JSONB, default=list)
    quality_scores: Mapped[dict] = mapped_column(JSONB, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class IndexingRunRecord(Base, TenantMixin):
    __tablename__ = "indexing_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_set_id: Mapped[str] = mapped_column(String(64), nullable=False)

    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    sparse_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    points_written: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Evaluation dataset (Sections 5.4, 14.2)
# ---------------------------------------------------------------------------


class EvaluationQuestionRecord(Base, TenantMixin):
    __tablename__ = "evaluation_questions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(64), default="default")
    question: Mapped[str] = mapped_column(Text, nullable=False)

    # Ground-truth evidence: chunk ids or document/page references
    expected_evidence: Mapped[list] = mapped_column(JSONB, default=list)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    section_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

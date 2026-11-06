"""SQLAlchemy declarative models (Section 9).

One model per proposal table. Versioning snapshot columns (9.7) are
included so every run can reproduce its results.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB, UUID


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )


class TenantMixin:
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


# ---------------------------------------------------------------------------
# Documents (Section 9.4)
# ---------------------------------------------------------------------------


class DocumentRecord(Base, TenantMixin):
    __tablename__ = "documents"

    id: Mapped = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped = mapped_column(UUID(as_uuid=True), nullable=False)

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)

    status: Mapped[str] = mapped_column(String(24), default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    quality_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    run_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)

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
    )


# ---------------------------------------------------------------------------
# Conversations (Section 9.2)
# ---------------------------------------------------------------------------


class ConversationRecord(Base, TenantMixin):
    __tablename__ = "conversations"

    id: Mapped = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped = mapped_column(UUID(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(256), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["MessageRecord"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class MessageRecord(Base, TenantMixin):
    __tablename__ = "messages"

    id: Mapped = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
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
        back_populates="messages"
    )

    __table_args__ = (
        Index("ix_messages_conversation", "conversation_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# Agent runs (Section 9.3)
# ---------------------------------------------------------------------------


class AgentRunRecord(Base, TenantMixin):
    __tablename__ = "agent_runs"

    id: Mapped = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped = mapped_column(UUID(as_uuid=True), nullable=False)

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
    )


# ---------------------------------------------------------------------------
# Versioned runs (Sections 9.5, 9.7)
# ---------------------------------------------------------------------------


class ChunkingRunRecord(Base, TenantMixin):
    __tablename__ = "chunking_runs"

    id: Mapped = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped = mapped_column(UUID(as_uuid=True), nullable=False)
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

    id: Mapped = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped = mapped_column(UUID(as_uuid=True), nullable=False)
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

    id: Mapped = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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

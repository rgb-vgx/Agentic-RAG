"""Retrieval domain types shared across the agentic pipeline."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievalStrategy(StrEnum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"


class EvidenceStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIALLY_SUFFICIENT = "PARTIALLY_SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"


class ResolvedQuery(BaseModel):
    """Structured output of conversation context resolution (Section 8.2)."""

    original_query: str
    standalone_query: str
    intent: str = "unknown"
    entities: list[str] = Field(default_factory=list)
    constraints: dict[str, str] = Field(default_factory=dict)
    conversation_context: str | None = None
    is_follow_up: bool = False


class RetrievalTask(BaseModel):
    task_id: str
    query: str
    retrieval_strategy: RetrievalStrategy = RetrievalStrategy.HYBRID


class RetrievalPlan(BaseModel):
    query_type: str = "simple"
    complexity: str = "low"
    retrieval_tasks: list[RetrievalTask] = Field(default_factory=list)
    requires_reranking: bool = False
    requires_context_expansion: bool = False
    max_iterations: int = 3


class RetrievedEvidence(BaseModel):
    """Output contract of every retrieval tool (Section 7.2)."""

    chunk_id: UUID
    document_id: UUID
    content: str
    score: float
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    reference_id: str = ""


class EvidenceGroup(BaseModel):
    """Evidence grouped per retrieval task, evaluated together."""

    task_id: str
    query: str
    evidence: list[RetrievedEvidence] = Field(default_factory=list)
    status: EvidenceStatus = EvidenceStatus.INSUFFICIENT
    missing_information: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)

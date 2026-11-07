"""Agent workflow state (Section 10 of the proposal).

The state carries authorization context, budgets and iteration limits —
never unrestricted credentials or provider secrets.
"""

from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app.domain.retrieval.types import (
    EvidenceGroup,
    EvidenceStatus,
    ResolvedQuery,
    RetrievalPlan,
)


class AgentState(TypedDict, total=False):
    run_id: str

    user_id: str
    tenant_id: str

    conversation_id: str
    user_query: str

    conversation_context: list[dict[str, Any]]

    # Produced by conversation context resolution
    resolved_query: dict[str, Any]

    query_analysis: dict[str, Any]
    retrieval_plan: dict[str, Any]

    # Complexity routing result (fast | standard | agentic), Section 15.1
    routing_path: str

    retrieval_results: list[dict[str, Any]]
    evidence_groups: list[dict[str, Any]]

    evidence_status: str

    iteration_count: int
    budget_remaining: bool

    final_answer: str | None
    citations: list[dict[str, Any]]

    # Set by validate_citations when the answer had to fall back to an
    # insufficiency message (claim-support check failed)
    validation_fallback: bool

    errors: list[dict[str, Any]]


class QueryAnalysis(BaseModel):
    """Structured result of the Query Analyzer node."""

    query_type: str = "simple_fact"  # simple_fact | conditional | multi_hop | comparison | summarization | follow_up | no_answer
    complexity: str = "low"  # low | medium | high
    entities: list[str] = Field(default_factory=list)
    required_scope: list[str] = Field(default_factory=list)  # knowledge base / document ids
    time_constraints: list[str] = Field(default_factory=list)
    evidence_granularity: str = "chunk"  # chunk | section | document
    requires_history: bool = False


class WorkflowLimits(BaseModel):
    """Execution budgets injected into state by the orchestrator."""

    max_retrieval_iterations: int = 3
    max_decomposition_tasks: int = 5
    max_evidence_tokens: int = 8000
    max_execution_ms: int = 60000


class EvidenceEvaluationResult(BaseModel):
    status: EvidenceStatus
    missing_information: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    rationale: str = ""

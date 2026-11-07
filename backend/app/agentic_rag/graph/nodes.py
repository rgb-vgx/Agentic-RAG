"""LangGraph node implementations (Section 10).

Nodes are plain async functions over AgentState. External dependencies
(LLM, retriever, embedder) arrive via NodeContext — never through state,
which must stay serializable for checkpointing.

Node flow:
    resolve_context → analyze → route → [plan → retrieve → rerank →
    evaluate → (loop | synthesize)] → validate_citations → persist
"""

import time
from dataclasses import dataclass, field
from typing import Any

from app.agentic_rag.evaluation.evidence import EvidenceEvaluator
from app.agentic_rag.planning import QueryAnalyzer, QueryResolver, RetrievalPlanner, route_path
from app.agentic_rag.retrieval.tools import ContextExpansionTool, HybridRetrievalTool, RerankTool
from app.agentic_rag.synthesis.synthesizer import AnswerSynthesizer
from app.agentic_rag.validation.citations import CitationValidator
from app.domain.agents.state import AgentState, WorkflowLimits
from app.domain.retrieval.types import EvidenceGroup, EvidenceStatus, RetrievalPlan
from app.config import settings
from app.infrastructure.llm.base import BaseLLMClient
from app.infrastructure.observability.langfuse_tracer import trace_node
from app.infrastructure.vectorstores.qdrant_store import HybridRetriever


@dataclass
class NodeContext:
    """Per-request dependency bundle for the workflow nodes."""

    llm: BaseLLMClient
    retriever: HybridRetriever
    embedder: Any  # BaseEmbeddingProvider
    limits: WorkflowLimits = field(default_factory=WorkflowLimits)

    resolver: QueryResolver | None = None
    analyzer: QueryAnalyzer | None = None
    planner: RetrievalPlanner | None = None
    evaluator: EvidenceEvaluator | None = None
    synthesizer: AnswerSynthesizer | None = None
    citation_validator: CitationValidator = field(default_factory=CitationValidator)
    hybrid_tool: HybridRetrievalTool | None = None
    rerank_tool: RerankTool | None = None
    expansion_tool: ContextExpansionTool | None = None

    def __post_init__(self):
        self.resolver = self.resolver or QueryResolver(self.llm)
        self.analyzer = self.analyzer or QueryAnalyzer(self.llm)
        self.planner = self.planner or RetrievalPlanner(
            self.llm, max_tasks=self.limits.max_decomposition_tasks
        )
        self.evaluator = self.evaluator or EvidenceEvaluator(self.llm)
        self.synthesizer = self.synthesizer or AnswerSynthesizer(self.llm)
        self.hybrid_tool = self.hybrid_tool or HybridRetrievalTool(self.retriever)
        self.rerank_tool = self.rerank_tool or RerankTool()
        self.expansion_tool = self.expansion_tool or ContextExpansionTool(self.retriever)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def resolve_context_node(state: AgentState, ctx: NodeContext) -> dict:
    """Conversation context resolution → ResolvedQuery (Section 8.2)."""
    async with trace_node("resolve_context", run_id=state.get("run_id")) as span:
        resolved = await ctx.resolver.resolve(
            state.get("conversation_context", []),
            state.get("user_query", ""),
        )
        span["output"] = {"standalone_query": resolved.standalone_query}
        return {"resolved_query": resolved.model_dump()}


async def analyze_node(state: AgentState, ctx: NodeContext) -> dict:
    """Query analysis → QueryAnalysis (Section 6.3)."""
    async with trace_node("analyze", run_id=state.get("run_id")) as span:
        from app.domain.retrieval.types import ResolvedQuery

        resolved = ResolvedQuery(**state["resolved_query"])
        analysis = await ctx.analyzer.analyze(resolved)
        span["output"] = analysis.model_dump()
        return {"query_analysis": analysis.model_dump()}


async def route_node(state: AgentState, ctx: NodeContext) -> dict:
    """Complexity routing (Section 15.1) → records the chosen path."""
    from app.domain.agents.state import QueryAnalysis

    analysis = QueryAnalysis(**state["query_analysis"])
    return {"routing_path": route_path(analysis)}


def route_after_analyze(state: AgentState) -> str:
    """Conditional edge: fast | standard | agentic → converge on plan."""
    from app.domain.agents.state import QueryAnalysis

    analysis = QueryAnalysis(**state["query_analysis"])
    return route_path(analysis)


async def plan_node(state: AgentState, ctx: NodeContext) -> dict:
    """Build the RetrievalPlan (Section 7.1).

    Note: iteration_count is intentionally NOT reset here — it counts
    completed retrieve+evaluate rounds across replans (see evaluate_node),
    which is what bounds the loop in should_continue.
    """
    async with trace_node("plan", run_id=state.get("run_id")) as span:
        from app.domain.agents.state import QueryAnalysis
        from app.domain.retrieval.types import ResolvedQuery

        resolved = ResolvedQuery(**state["resolved_query"])
        analysis = QueryAnalysis(**state["query_analysis"])
        plan = await ctx.planner.plan(resolved, analysis)
        span["output"] = plan.model_dump()
        return {"retrieval_plan": plan.model_dump()}


async def retrieve_node(state: AgentState, ctx: NodeContext) -> dict:
    """Run every retrieval task, then rerank (Sections 7.2, 11)."""
    async with trace_node("retrieve", run_id=state.get("run_id")) as span:
        plan = RetrievalPlan(**state["retrieval_plan"])
        resolved = state["resolved_query"]
        query_vector = await ctx.embedder.embed_query(
            resolved.get("standalone_query", state.get("user_query", ""))
        )

        # Restrict to documents the caller may see (None = legacy/no filter).
        # An empty snapshot must never fall back to a tenant-wide search.
        snapshot = state.get("snapshot_document_ids")
        groups: list[EvidenceGroup] = []
        for task in plan.retrieval_tasks:
            if snapshot is not None and not snapshot:
                evidence = []
            else:
                evidence = await ctx.hybrid_tool.run(
                    query=task.query,
                    tenant_id=state["tenant_id"],
                    query_vector=query_vector,
                    limit=20,
                    document_ids=snapshot,
                )
                # Re-embed sub-query for tasks that differ from the main query
                if task.query != resolved.get("standalone_query"):
                    sub_vector = await ctx.embedder.embed_query(task.query)
                    evidence = await ctx.hybrid_tool.run(
                        query=task.query,
                        tenant_id=state["tenant_id"],
                        query_vector=sub_vector,
                        limit=20,
                        document_ids=snapshot,
                    )

            if plan.requires_reranking and evidence:
                evidence = await ctx.rerank_tool.run(
                    task.query, evidence, top_k=8
                )

            if plan.requires_context_expansion and evidence:
                expanded = await ctx.expansion_tool.run(
                    evidence[0], state["tenant_id"]
                )
                evidence = evidence + expanded

            groups.append(
                EvidenceGroup(
                    task_id=task.task_id,
                    query=task.query,
                    evidence=evidence,
                )
            )

        # Reference ids are assigned after final ordering for stable citations
        counter = 1
        for group in groups:
            for evidence in group.evidence:
                evidence.reference_id = f"R{counter}"
                counter += 1

        span["output"] = {
            "groups": len(groups),
            "evidence": sum(len(g.evidence) for g in groups),
        }
        return {
            "evidence_groups": [g.model_dump(mode="json") for g in groups],
        }


async def evaluate_node(state: AgentState, ctx: NodeContext) -> dict:
    """Evidence sufficiency check per group (Section 7.3)."""
    async with trace_node("evaluate", run_id=state.get("run_id")) as span:
        groups = [EvidenceGroup(**g) for g in state.get("evidence_groups", [])]
        statuses: list[str] = []
        missing: list[str] = []
        contradictions: list[str] = []

        for group in groups:
            result = await ctx.evaluator.evaluate(group)
            group.status = result.status
            group.missing_information = result.missing_information
            group.contradictions = result.contradictions
            statuses.append(result.status.value)
            missing.extend(result.missing_information)
            contradictions.extend(result.contradictions)

        overall = _overall_status(statuses)
        span["output"] = {"status": overall, "missing": len(missing)}
        return {
            "evidence_groups": [g.model_dump(mode="json") for g in groups],
            "evidence_status": overall,
            "missing_information": missing,
            "contradictions": contradictions,
            # One bounded round completed — this is the loop counter
            # (incremented in exactly one place so replans can't double-count)
            "iteration_count": state.get("iteration_count", 0) + 1,
        }


def should_continue(state: AgentState) -> str:
    """Conditional edge: iterate when evidence is insufficient (bounded)."""
    status = state.get("evidence_status", EvidenceStatus.INSUFFICIENT.value)
    iteration = state.get("iteration_count", 0)

    # The plan may ask for more rounds, but the server-side config is the
    # hard cap — a prompt-injected or hallucinated max_iterations must not
    # be able to extend the loop.
    plan = state.get("retrieval_plan") or {}
    try:
        requested = int(plan.get("max_iterations", 3))
    except (TypeError, ValueError):
        requested = 3
    max_iterations = max(1, min(requested, settings.agent_max_retrieval_iterations))

    if status in (
        EvidenceStatus.SUFFICIENT.value,
        EvidenceStatus.PARTIALLY_SUFFICIENT.value,
        EvidenceStatus.CONFLICTING.value,
    ):
        return "synthesize"
    if iteration >= max_iterations:
        return "synthesize"  # bounded — never loop forever (Section 10)
    return "plan"  # replan with missing_information as guidance


async def synthesize_node(state: AgentState, ctx: NodeContext) -> dict:
    """Generate the grounded answer (Section 7.4)."""
    async with trace_node("synthesize", run_id=state.get("run_id")) as span:
        groups = [EvidenceGroup(**g) for g in state.get("evidence_groups", [])]
        resolved = state["resolved_query"]
        query = resolved.get("standalone_query", state.get("user_query", ""))

        answer = await ctx.synthesizer.synthesize(query, groups)
        span["output"] = {"answer_chars": len(answer)}
        return {"final_answer": answer}


async def validate_citations_node(state: AgentState, ctx: NodeContext) -> dict:
    """Deterministic citation validation with claim-support check (7.5)."""
    async with trace_node("validate_citations", run_id=state.get("run_id")) as span:
        answer = state.get("final_answer") or ""
        groups = [EvidenceGroup(**g) for g in state.get("evidence_groups", [])]

        result = ctx.citation_validator.validate(
            answer,
            groups,
            user_query=state.get("user_query", ""),
        )
        span["output"] = {
            "sources": len(result.sources),
            "invalid": result.invalid_references,
            "dropped_claims": len(result.dropped_claims),
            "fallback": result.fallback,
        }
        return {
            "final_answer": result.answer,
            "citations": [s.model_dump(mode="json") for s in result.sources],
            "invalid_citations": result.invalid_references,
            "validation_fallback": result.fallback,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _overall_status(statuses: list[str]) -> str:
    if not statuses:
        return EvidenceStatus.INSUFFICIENT.value
    if all(s == EvidenceStatus.SUFFICIENT.value for s in statuses):
        return EvidenceStatus.SUFFICIENT.value
    if any(s == EvidenceStatus.CONFLICTING.value for s in statuses):
        return EvidenceStatus.CONFLICTING.value
    if any(s == EvidenceStatus.INSUFFICIENT.value for s in statuses):
        if all(s == EvidenceStatus.INSUFFICIENT.value for s in statuses):
            return EvidenceStatus.INSUFFICIENT.value
        return EvidenceStatus.PARTIALLY_SUFFICIENT.value
    return EvidenceStatus.PARTIALLY_SUFFICIENT.value

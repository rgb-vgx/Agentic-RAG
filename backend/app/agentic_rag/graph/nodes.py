"""LangGraph node implementations (Section 10).

Nodes are plain async functions over AgentState. External dependencies
(LLM, retriever, embedder) arrive via NodeContext — never through state,
which must stay serializable for checkpointing.

Node flow:
    resolve_context → analyze → route → [plan → retrieve → rerank →
    evaluate → (loop | synthesize)] → validate_citations → persist
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.agentic_rag.evaluation.evidence import EvidenceEvaluator
from app.agentic_rag.planning import QueryAnalyzer, QueryResolver, RetrievalPlanner, route_path
from app.agentic_rag.retrieval.tools import (
    ContextExpansionTool,
    ExactLegalReferenceTool,
    HybridRetrievalTool,
    RerankTool,
)
from app.agentic_rag.synthesis.synthesizer import AnswerSynthesizer
from app.agentic_rag.validation.citations import CitationValidator
from app.config import settings
from app.domain.agents.state import AgentState, WorkflowLimits
from app.domain.retrieval.types import (
    EvidenceGroup,
    EvidenceStatus,
    RetrievalPlan,
    RetrievedEvidence,
)
from app.infrastructure.llm.base import BaseLLMClient, LLMMessage
from app.infrastructure.observability.langfuse_tracer import trace_node
from app.infrastructure.vectorstores.qdrant_store import HybridRetriever
from app.ingestion.legal_metadata import (
    document_metadata_payload,
    has_explicit_document_hint,
    is_pure_reference,
    parse_legal_reference,
    resolve_document,
    resolve_document_from_context,
)

logger = logging.getLogger(__name__)


@dataclass
class NodeContext:
    """Per-request dependency bundle for the workflow nodes."""

    llm: BaseLLMClient
    retriever: HybridRetriever
    embedder: Any  # BaseEmbeddingProvider
    limits: WorkflowLimits = field(default_factory=WorkflowLimits)
    # Visible documents (identity metadata) for exact legal references.
    documents: list[dict] = field(default_factory=list)

    resolver: QueryResolver | None = None
    analyzer: QueryAnalyzer | None = None
    planner: RetrievalPlanner | None = None
    evaluator: EvidenceEvaluator | None = None
    synthesizer: AnswerSynthesizer | None = None
    citation_validator: CitationValidator = field(default_factory=CitationValidator)
    hybrid_tool: HybridRetrievalTool | None = None
    rerank_tool: RerankTool | None = None
    expansion_tool: ContextExpansionTool | None = None
    exact_tool: ExactLegalReferenceTool | None = None

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
        self.exact_tool = self.exact_tool or ExactLegalReferenceTool(self.retriever)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _looks_complex_query(query: str) -> bool:
    folded = query.lower()
    return any(
        marker in folded
        for marker in (
            "so sánh",
            "khác nhau",
            "liệt kê",
            "tất cả",
            "nhiều tài liệu",
            "bảng",
            "multi",
            "compare",
        )
    )


async def resolve_context_node(state: AgentState, ctx: NodeContext) -> dict:
    """Conversation context resolution → ResolvedQuery (Section 8.2)."""
    async with trace_node("resolve_context", run_id=state.get("run_id")) as span:
        if (
            state.get("fast_document_scope")
            and not state.get("conversation_context")
        ):
            from app.domain.retrieval.types import ResolvedQuery

            query = state.get("user_query", "")
            resolved = ResolvedQuery(
                original_query=query,
                standalone_query=query,
            )
        else:
            resolved = await ctx.resolver.resolve(
                state.get("conversation_context", []),
                state.get("user_query", ""),
            )
        span["output"] = {
            "standalone_query": resolved.standalone_query,
            "fast_scope": bool(state.get("fast_document_scope")),
        }
        return {"resolved_query": resolved.model_dump()}


async def analyze_node(state: AgentState, ctx: NodeContext) -> dict:
    """Query analysis → QueryAnalysis (Section 6.3)."""
    async with trace_node("analyze", run_id=state.get("run_id")) as span:
        from app.domain.agents.state import QueryAnalysis
        from app.domain.retrieval.types import ResolvedQuery

        resolved = ResolvedQuery(**state["resolved_query"])
        if (
            state.get("fast_document_scope")
            and not state.get("conversation_context")
            and not _looks_complex_query(state.get("user_query", ""))
        ):
            analysis = QueryAnalysis(
                query_type="simple_fact",
                complexity="low",
                requires_history=False,
            )
        else:
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
    """Run retrieval tasks, then rerank (Sections 7.2, 11).

    Exact legal references ("Điều 19 Bộ luật tố tụng dân sự") are fetched
    from metadata first. Structural candidates are never pushed through the
    semantic top-K truncation; pure citation queries skip embeddings.
    """
    async with trace_node("retrieve", run_id=state.get("run_id")) as span:
        plan = RetrievalPlan(**state["retrieval_plan"])
        resolved = state["resolved_query"]
        query_text = resolved.get("standalone_query", state.get("user_query", ""))

        # Restrict to documents the caller may see (None = legacy/no filter).
        # An empty snapshot must never fall back to a tenant-wide search.
        snapshot = state.get("snapshot_document_ids")

        reference = parse_legal_reference(query_text)
        exact_evidence: list[RetrievedEvidence] = []
        resolved_document: dict | None = None
        if reference is not None:
            documents = document_metadata_payload(ctx.documents)
            resolved_document, _ambiguous = resolve_document(reference, documents)
            if resolved_document is None and not has_explicit_document_hint(reference):
                resolved_document = resolve_document_from_context(
                    documents, state.get("conversation_context")
                )
            # If the query names a document that is not in this knowledge base,
            # do not fall back to an article from a different law.
            unresolved_hint = (
                has_explicit_document_hint(reference)
                and resolved_document is None
            )
            if (snapshot is not None and not snapshot) or unresolved_hint:
                exact_evidence = []
            else:
                exact_evidence = await ctx.exact_tool.run(
                    reference,
                    tenant_id=state["tenant_id"],
                    documents=documents,
                    document_ids=snapshot,
                    resolved_document_id=(
                        str(resolved_document["id"]) if resolved_document else None
                    ),
                )

        # A pure citation query needs no embedding and no semantic truncation.
        if (
            reference is not None
            and exact_evidence
            and is_pure_reference(query_text, reference, resolved_document)
        ):
            task = plan.retrieval_tasks[0]
            groups = [
                EvidenceGroup(
                    task_id=task.task_id,
                    query=query_text,
                    evidence=exact_evidence,
                )
            ]
            _assign_reference_ids(groups)
            clarification = None
            if resolved_document is None and len(exact_evidence) > 1:
                clarification = _build_clarification(reference, exact_evidence)
            span["output"] = {
                "groups": 1,
                "evidence": len(exact_evidence),
                "exact_reference": True,
                "clarification": bool(clarification),
            }
            return {
                "evidence_groups": [g.model_dump(mode="json") for g in groups],
                "clarification": clarification,
            }

        query_vector = await ctx.embedder.embed_query(query_text)
        rerank_slots = asyncio.Semaphore(2)

        async def retrieve_one(task) -> EvidenceGroup:
            if snapshot is not None and not snapshot:
                evidence = []
            else:
                evidence = await ctx.hybrid_tool.run(
                    query=task.query,
                    tenant_id=state["tenant_id"],
                    query_vector=query_vector,
                    limit=ctx.limits.retrieval_candidates,
                    document_ids=snapshot,
                )
                # Re-embed sub-query for tasks that differ from the main query.
                if task.query != resolved.get("standalone_query"):
                    sub_vector = await ctx.embedder.embed_query(task.query)
                    evidence = await ctx.hybrid_tool.run(
                        query=task.query,
                        tenant_id=state["tenant_id"],
                        query_vector=sub_vector,
                        limit=ctx.limits.retrieval_candidates,
                        document_ids=snapshot,
                    )

            # Rerank whenever configured, with a small concurrency cap so the
            # GPU reranker is not hit by every sub-query at once.
            if (
                ctx.rerank_tool.reranker is not None
                and evidence
                and not (
                    state.get("fast_document_scope")
                    and plan.complexity == "low"
                )
            ):
                async with rerank_slots:
                    evidence = await ctx.rerank_tool.run(
                        task.query, evidence, top_k=ctx.limits.rerank_top_n
                    )

            if plan.requires_context_expansion and evidence:
                expanded = await ctx.expansion_tool.run(
                    evidence[0], state["tenant_id"]
                )
                evidence = evidence + expanded

            return EvidenceGroup(
                task_id=task.task_id,
                query=task.query,
                evidence=evidence,
            )

        # Sub-queries are independent: run them concurrently and preserve
        # planner order for the evidence pack.
        groups = list(
            await asyncio.gather(
                *(retrieve_one(task) for task in plan.retrieval_tasks)
            )
        )

        # Mixed query: preserve structural candidates ahead of semantic ones.
        if exact_evidence and groups:
            seen = {item.chunk_id for item in exact_evidence}
            semantic = [item for item in groups[0].evidence if item.chunk_id not in seen]
            groups[0].evidence = exact_evidence + semantic

        web_raw = state.get("web_evidence") or []
        if web_raw:
            web_items = [RetrievedEvidence(**item) for item in web_raw]
            groups.append(
                EvidenceGroup(
                    task_id="web",
                    query=query_text,
                    evidence=web_items,
                )
            )

        _assign_reference_ids(groups)

        span["output"] = {
            "groups": len(groups),
            "evidence": sum(len(g.evidence) for g in groups),
            "exact_reference": bool(exact_evidence),
        }
        return {
            "evidence_groups": [g.model_dump(mode="json") for g in groups],
        }


def _build_clarification(
    reference, evidence: list[RetrievedEvidence]
) -> dict[str, Any]:
    """Ask which document a bare article reference belongs to."""
    options = []
    for item in evidence:
        document_title = item.metadata.get("document_title")
        if not document_title and len(item.heading_path) > 1:
            document_title = item.heading_path[0]
        article_title = item.metadata.get("article_title")
        options.append(
            {
                "document_id": str(item.document_id),
                "document_title": document_title or "",
                "article_title": article_title or "",
            }
        )

    lines = [f"Điều {reference.article_number} có trong nhiều văn bản:"]
    for option in options:
        title = option["document_title"] or "Không rõ tên văn bản"
        article_title = f" — {option['article_title']}" if option["article_title"] else ""
        lines.append(f"- **{title}**{article_title}")
    lines.append("Bạn muốn xem văn bản nào?")
    return {
        "question": "\n".join(lines),
        "article_number": reference.article_number,
        "options": options,
    }


def _assign_reference_ids(groups: list[EvidenceGroup]) -> None:
    """Assign stable R ids after final ordering."""
    counter = 1
    for group in groups:
        for evidence in group.evidence:
            evidence.reference_id = f"R{counter}"
            counter += 1


async def evaluate_node(state: AgentState, ctx: NodeContext) -> dict:
    """Evidence sufficiency check per group (Section 7.3)."""
    async with trace_node("evaluate", run_id=state.get("run_id")) as span:
        if state.get("clarification"):
            span["output"] = {"status": "clarification", "missing": 0}
            return {
                "evidence_status": EvidenceStatus.SUFFICIENT.value,
                "iteration_count": state.get("iteration_count", 0) + 1,
            }
        groups = [EvidenceGroup(**g) for g in state.get("evidence_groups", [])]
        if (
            state.get("fast_document_scope")
            and state.get("query_analysis", {}).get("complexity") == "low"
        ):
            has_evidence = any(group.evidence for group in groups)
            status = (
                EvidenceStatus.SUFFICIENT.value
                if has_evidence
                else EvidenceStatus.INSUFFICIENT.value
            )
            for group in groups:
                group.status = EvidenceStatus(status)
            span["output"] = {"status": status, "fast_scope": True}
            return {
                "evidence_groups": [g.model_dump(mode="json") for g in groups],
                "evidence_status": status,
                "missing_information": [],
                "contradictions": [],
                "iteration_count": state.get("iteration_count", 0) + 1,
            }

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
        clarification = state.get("clarification")
        if clarification:
            answer = clarification.get("question", "")
            span["output"] = {"answer_chars": len(answer), "clarification": True}
            return {"final_answer": answer}
        groups = [EvidenceGroup(**g) for g in state.get("evidence_groups", [])]
        # Keep the prompt inside the local model's context window: Ollama
        # truncates from the start, which drops the system prompt and the
        # best-ranked evidence before the model ever sees them.
        limit = ctx.limits.max_evidence_per_group
        if limit > 0:
            groups = [
                group.model_copy(update={"evidence": group.evidence[:limit]})
                for group in groups
            ]
        resolved = state["resolved_query"]
        query = resolved.get("standalone_query", state.get("user_query", ""))

        answer = await ctx.synthesizer.synthesize(query, groups)
        repaired = False
        if (
            any(group.evidence for group in groups)
            and not re.search(r"\[R\d+\]", answer)
        ):
            # A model occasionally ignores the citation rule. One strict retry
            # is cheaper than returning the generic insufficiency fallback.
            messages = ctx.synthesizer.build_messages(query, groups)
            messages.append(
                LLMMessage(
                    role="user",
                    content=(
                        "Câu trả lời trước không có trích dẫn nên không dùng được. "
                        "Hãy trả lời lại và gắn [R1], [R2]... vào từng câu khẳng "
                        "định, chỉ dùng các mã có trong bằng chứng."
                    ),
                )
            )
            retry = await ctx.llm.complete(
                messages,
                temperature=0.0,
                max_tokens=settings.synthesis_max_tokens,
            )
            if re.search(r"\[R\d+\]", retry.content):
                answer = retry.content
                repaired = True
        span["output"] = {"answer_chars": len(answer), "citation_retry": repaired}
        return {"final_answer": answer}


async def validate_citations_node(state: AgentState, ctx: NodeContext) -> dict:
    """Deterministic citation validation with claim-support check (7.5)."""
    async with trace_node("validate_citations", run_id=state.get("run_id")) as span:
        if state.get("clarification"):
            span["output"] = {"clarification": True}
            return {
                "final_answer": state.get("final_answer") or "",
                "citations": [],
                "invalid_citations": [],
                "validation_fallback": False,
            }
        answer = state.get("final_answer") or ""
        groups = [EvidenceGroup(**g) for g in state.get("evidence_groups", [])]

        result = ctx.citation_validator.validate(
            answer,
            groups,
            user_query=state.get("user_query", ""),
        )
        if result.fallback:
            logger.warning(
                "citation validation fallback: dropped=%d invalid=%d query=%r",
                len(result.dropped_claims),
                len(result.invalid_references),
                state.get("user_query", "")[:200],
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

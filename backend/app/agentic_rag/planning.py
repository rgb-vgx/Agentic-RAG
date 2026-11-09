"""Query understanding and planning (Sections 6.3, 7.1, 8.2).

Pipeline: ResolvedQuery → QueryAnalysis → RetrievalPlan.
The LangGraph nodes call into this module; each step is a single LLM call
with structured JSON output and deterministic fallbacks.
"""

import json

from app.agentic_rag import prompts
from app.config import settings
from app.domain.agents.state import QueryAnalysis
from app.domain.retrieval.types import ResolvedQuery, RetrievalPlan, RetrievalStrategy
from app.infrastructure.llm.base import BaseLLMClient, LLMMessage


class QueryResolver:
    """Conversation context resolution → ResolvedQuery (Section 8.2)."""

    def __init__(self, llm: BaseLLMClient):
        self.llm = llm

    async def resolve(
        self, history: list[dict], user_query: str
    ) -> ResolvedQuery:
        if not history:
            return ResolvedQuery(
                original_query=user_query,
                standalone_query=user_query,
                is_follow_up=False,
            )

        history_text = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history[-6:]
        )
        try:
            data = await self.llm.complete_json(
                [
                    LLMMessage(
                        role="system",
                        content=prompts.RESOLVE_QUERY.template.format(
                            history=history_text, query=user_query
                        ),
                    )
                ],
                max_tokens=800,
            )
            return ResolvedQuery(
                original_query=user_query,
                standalone_query=data.get("standalone_query", user_query),
                intent=data.get("intent", "unknown"),
                entities=data.get("entities", []),
                constraints=data.get("constraints", {}),
                conversation_context=history_text or None,
                is_follow_up=bool(data.get("is_follow_up", True)),
            )
        except (ValueError, json.JSONDecodeError):
            # Deterministic fallback: treat as follow-up with raw query
            return ResolvedQuery(
                original_query=user_query,
                standalone_query=user_query,
                conversation_context=history_text or None,
                is_follow_up=True,
            )


class QueryAnalyzer:
    """ResolvedQuery → QueryAnalysis (Section 6.3)."""

    def __init__(self, llm: BaseLLMClient):
        self.llm = llm

    async def analyze(self, resolved: ResolvedQuery) -> QueryAnalysis:
        try:
            data = await self.llm.complete_json(
                [
                    LLMMessage(
                        role="system",
                        content=prompts.ANALYZE_QUERY.template.format(
                            query=resolved.standalone_query
                        ),
                    )
                ],
                max_tokens=600,
            )
            return QueryAnalysis(
                query_type=data.get("query_type", "simple_fact"),
                complexity=data.get("complexity", "low"),
                entities=data.get("entities", resolved.entities),
                required_scope=data.get("required_scope", []),
                time_constraints=data.get("time_constraints", []),
                evidence_granularity=data.get("evidence_granularity", "chunk"),
                requires_history=bool(data.get("requires_history", False)),
            )
        except (ValueError, json.JSONDecodeError):
            # Conservative fallback: standard path, single hybrid task
            return QueryAnalysis(complexity="medium", query_type="simple_fact")


class RetrievalPlanner:
    """QueryAnalysis → RetrievalPlan (Section 7.1)."""

    def __init__(self, llm: BaseLLMClient, max_tasks: int = 5):
        self.llm = llm
        self.max_tasks = max_tasks

    async def plan(
        self, resolved: ResolvedQuery, analysis: QueryAnalysis
    ) -> RetrievalPlan:
        # Fast/standard paths don't need an LLM plan. Simple factual questions
        # also stay on one retrieval task: decomposing them into 3 sub-queries
        # triples rerank latency for no recall gain.
        if analysis.complexity == "low" or analysis.query_type == "simple_fact":
            return RetrievalPlan(
                query_type=analysis.query_type,
                complexity=analysis.complexity,
                retrieval_tasks=[_task("t1", resolved.standalone_query)],
                requires_reranking=analysis.complexity != "low",
                max_iterations=1,
            )

        try:
            data = await self.llm.complete_json(
                [
                    LLMMessage(
                        role="system",
                        content=prompts.PLAN_RETRIEVAL.template.format(
                            query=resolved.standalone_query,
                            analysis=analysis.model_dump_json(),
                            max_tasks=self.max_tasks,
                        ),
                    )
                ],
                max_tokens=1200,
            )
            tasks = data.get("retrieval_tasks", [])[: self.max_tasks]
            if not tasks:
                tasks = [_task("t1", resolved.standalone_query)]
            return RetrievalPlan(
                query_type=data.get("query_type", analysis.query_type),
                complexity=data.get("complexity", analysis.complexity),
                retrieval_tasks=tasks,
                requires_reranking=bool(data.get("requires_reranking", True)),
                requires_context_expansion=bool(
                    data.get("requires_context_expansion", False)
                ),
                # Server-side cap — never derived from model output
                max_iterations=settings.agent_max_retrieval_iterations,
            )
        except (ValueError, json.JSONDecodeError):
            return RetrievalPlan(
                query_type=analysis.query_type,
                complexity=analysis.complexity,
                retrieval_tasks=[_task("t1", resolved.standalone_query)],
                requires_reranking=True,
            )


def _task(task_id: str, query: str) -> dict:
    return {
        "task_id": task_id,
        "query": query,
        "retrieval_strategy": RetrievalStrategy.HYBRID.value,
    }


def route_path(analysis: QueryAnalysis) -> str:
    """Complexity routing (Section 15.1): fast | standard | agentic."""
    if analysis.complexity == "low" and analysis.query_type == "simple_fact":
        return "fast"
    if analysis.complexity == "high" or analysis.query_type in (
        "multi_hop",
        "comparison",
        "summarization",
    ):
        return "agentic"
    return "standard"

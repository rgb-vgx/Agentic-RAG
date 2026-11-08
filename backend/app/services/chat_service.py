"""Chat service — bridges API, workflow and SSE (Sections 10, 12).

The service owns the SSE frame contract: graph node updates become
`status` frames, the validated answer becomes `token` frames, citations
follow, and the run ends with `completed` or `error`.

Persistence is intentionally left to the API layer so a database session is
not held open for the lifetime of the stream. The selected provider/model
is used to build one `NodeContext` per request; every node shares that
client, so OpenAI and Anthropic follow the same code path.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from time import monotonic
from typing import Any

from app.agentic_rag.graph.nodes import NodeContext
from app.agentic_rag.graph.workflow import build_graph, initial_state, stream_run
from app.agentic_rag.retrieval.tools import RerankTool
from app.config import settings
from app.domain.agents.state import WorkflowLimits
from app.infrastructure.embeddings import get_embedding_provider
from app.infrastructure.indexing_factory import build_retriever
from app.infrastructure.llm import get_llm_client
from app.infrastructure.rerankers import build_reranker


@dataclass
class ChatRunResult:
    """What the API layer needs to persist a finished run."""

    answer: str
    citations: list[dict[str, Any]]
    routing_path: str
    iterations: int
    latency_ms: int


def build_node_context(provider: str, model: str) -> NodeContext:
    """Build per-request dependencies, failing fast on missing credentials.

    `provider`/`model` come from the server-side allowlist, never directly
    from the client.
    """
    if provider == "openai" and not (settings.openai_api_key or settings.openai_base_url):
        raise ValueError(
            "OpenAI is not configured (set OPENAI_API_KEY or OPENAI_BASE_URL)"
        )
    if provider == "anthropic" and not (
        settings.anthropic_api_key
        or settings.anthropic_auth_token
        or settings.anthropic_base_url
    ):
        raise ValueError(
            "Anthropic is not configured (set ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL)"
        )
    if settings.embedding_provider == "openai" and not (
        settings.openai_api_key
        or settings.openai_base_url
        or settings.embedding_base_url
    ):
        raise ValueError(
            "OpenAI embeddings are not configured (set OPENAI_API_KEY/OPENAI_BASE_URL "
            "or EMBEDDING_PROVIDER=local)"
        )

    return NodeContext(
        llm=get_llm_client(provider, model=model),
        retriever=build_retriever(),
        embedder=get_embedding_provider(),
        limits=WorkflowLimits(
            max_retrieval_iterations=settings.agent_max_retrieval_iterations,
            max_decomposition_tasks=settings.agent_max_decomposition_tasks,
            max_evidence_per_group=settings.synthesis_max_evidence_per_group,
            retrieval_candidates=(
                settings.rerank_candidates if settings.rerank_enabled else 20
            ),
            rerank_top_n=settings.rerank_top_n,
        ),
        rerank_tool=RerankTool(build_reranker()),
    )


class ChatService:
    """Runs one agent query and yields SSE-ready events."""

    def __init__(self, ctx: NodeContext):
        self.ctx = ctx
        self.graph = build_graph(ctx)
        self.last_result: ChatRunResult | None = None
        self.last_error: str | None = None

    async def stream(
        self,
        *,
        run_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
        user_query: str,
        conversation_context: list[dict[str, Any]] | None = None,
        document_ids: list[str] | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        """Yield {"event": ..., "data": json} dicts — the SSE contract.

        `run_started` is emitted by the API layer, which owns the run row
        and the visibility snapshot.
        """
        self.last_result = None
        self.last_error = None
        started = monotonic()

        state = initial_state(
            run_id=run_id,
            user_id=user_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_query=user_query,
            conversation_context=conversation_context,
            limits=self.ctx.limits,
            document_ids=document_ids,
        )

        try:
            final_state: dict[str, Any] = {}
            async for event, payload in stream_run(self.graph, state):
                if event == "status":
                    yield _sse("status", payload)
                elif event == "done":
                    final_state = payload

            answer = final_state.get("final_answer") or ""
            citations = list(final_state.get("citations") or [])

            # Tokens are emitted only after citation validation: the backend
            # owns citation truth (Section 7.5).
            for index in range(0, len(answer), 40):
                yield _sse("token", {"delta": answer[index : index + 40]})

            for citation in citations:
                yield _sse("citation", citation)

            result = ChatRunResult(
                answer=answer,
                citations=citations,
                routing_path=final_state.get("routing_path", ""),
                iterations=int(final_state.get("iteration_count", 0)),
                latency_ms=int((monotonic() - started) * 1000),
            )
            self.last_result = result
            yield _sse(
                "completed",
                {
                    "run_id": run_id,
                    "routing_path": result.routing_path,
                    "iterations": result.iterations,
                    "citations": len(citations),
                    "latency_ms": result.latency_ms,
                },
            )
        except Exception as exc:  # surface workflow failure as an SSE error
            self.last_error = f"{type(exc).__name__}: {exc}"
            yield _sse("error", {"message": self.last_error})


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    import json

    return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}

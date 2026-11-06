"""Chat service — bridges API, workflow and persistence (Sections 10, 12).

Owns the SSE event contract: converts graph node updates into `status`
frames and synthesizer deltas into `token` frames, validates citations
before `completed`, and persists messages/agent runs.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from app.agentic_rag.graph.nodes import NodeContext
from app.agentic_rag.graph.workflow import build_graph, initial_state, stream_run
from app.domain.agents.state import WorkflowLimits
from app.config import settings


class ChatService:
    """Runs one agent query and yields SSE-ready events."""

    def __init__(self, ctx: NodeContext):
        self.ctx = ctx
        self.graph = build_graph(ctx)

    async def stream(
        self,
        *,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
        user_query: str,
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        """Yield {"event": ..., "data": json} dicts — the SSE contract."""
        run_id = str(uuid4())
        limits = WorkflowLimits(
            max_retrieval_iterations=settings.agent_max_retrieval_iterations,
            max_decomposition_tasks=settings.agent_max_decomposition_tasks,
        )

        yield _sse("run_started", {"run_id": run_id})

        state = initial_state(
            run_id=run_id,
            user_id=user_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_query=user_query,
            conversation_context=conversation_context,
            limits=limits,
        )

        try:
            final_state: dict[str, Any] = {}
            async for event, payload in stream_run(self.graph, state):
                if event == "status":
                    yield _sse("status", payload)
                elif event == "done":
                    final_state = payload

            answer = final_state.get("final_answer") or ""
            citations = final_state.get("citations", [])

            # Token frames are emitted from the final answer; the graph's
            # synthesizer node runs non-streaming so citation validation can
            # happen before any content reaches the user (Section 7.5).
            for index in range(0, len(answer), 40):
                yield _sse("token", {"delta": answer[index : index + 40]})

            for citation in citations:
                yield _sse("citation", citation)

            yield _sse(
                "completed",
                {
                    "run_id": run_id,
                    "routing_path": final_state.get("routing_path", ""),
                    "citations": len(citations),
                },
            )
        except Exception as exc:  # surface workflow failure as an SSE error
            yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    import json

    return {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}

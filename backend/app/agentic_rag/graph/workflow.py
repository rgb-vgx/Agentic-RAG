"""LangGraph workflow assembly (Section 10).

StateGraph with a centralized orchestrator — no multi-agent swarm.
Bounded iterations enforced by `should_continue` + WorkflowLimits.

Streaming: the service layer calls `graph.astream(...)` and forwards node
updates as SSE frames (status events) plus synthesizer token deltas.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agentic_rag.graph.nodes import (
    NodeContext,
    analyze_node,
    evaluate_node,
    plan_node,
    resolve_context_node,
    retrieve_node,
    route_node,
    should_continue,
    synthesize_node,
    validate_citations_node,
)
from app.domain.agents.state import AgentState, WorkflowLimits


def build_graph(ctx: NodeContext):
    """Compile the agentic RAG StateGraph with the given dependencies."""
    workflow = StateGraph(AgentState)

    # Nodes take only state; ctx is closed over via partial-style wrappers
    def node(fn):
        async def wrapper(state: AgentState) -> dict:
            return await fn(state, ctx)

        wrapper.__name__ = fn.__name__
        return wrapper

    workflow.add_node("resolve_context", node(resolve_context_node))
    workflow.add_node("analyze", node(analyze_node))
    workflow.add_node("route", node(route_node))
    workflow.add_node("plan", node(plan_node))
    workflow.add_node("retrieve", node(retrieve_node))
    workflow.add_node("evaluate", node(evaluate_node))
    workflow.add_node("synthesize", node(synthesize_node))
    workflow.add_node("validate_citations", node(validate_citations_node))

    # Entry: context resolution → analysis → routing
    workflow.add_edge(START, "resolve_context")
    workflow.add_edge("resolve_context", "analyze")
    workflow.add_edge("analyze", "route")

    # All three paths converge on plan; plan → retrieve → evaluate
    workflow.add_edge("route", "plan")
    workflow.add_edge("plan", "retrieve")
    workflow.add_edge("retrieve", "evaluate")

    # Bounded loop: evaluate → plan (retry) | synthesize
    workflow.add_conditional_edges(
        "evaluate",
        should_continue,
        {"plan": "plan", "synthesize": "synthesize"},
    )

    workflow.add_edge("synthesize", "validate_citations")
    workflow.add_edge("validate_citations", END)

    return workflow.compile()


def initial_state(
    *,
    run_id: str,
    user_id: str,
    tenant_id: str,
    conversation_id: str,
    user_query: str,
    conversation_context: list[dict[str, Any]] | None = None,
    limits: WorkflowLimits | None = None,
    document_ids: list[str] | None = None,
    web_evidence: list[dict[str, Any]] | None = None,
) -> AgentState:
    """Seed state for one agent run (Section 10)."""
    limits = limits or WorkflowLimits()
    return AgentState(
        run_id=run_id,
        user_id=user_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        user_query=user_query,
        snapshot_document_ids=document_ids,
        conversation_context=conversation_context or [],
        resolved_query={},
        query_analysis={},
        retrieval_plan={},
        retrieval_results=[],
        evidence_groups=[],
        web_evidence=web_evidence or [],
        evidence_status="",
        iteration_count=0,
        budget_remaining=True,
        final_answer=None,
        citations=[],
        clarification=None,
        errors=[],
        routing_path="",
    )


async def stream_run(graph, state: AgentState):
    """Yield (event, payload) tuples from the graph stream.

    Events: status (node updates), done (final state). The final state is
    accumulated from the update stream — the graph has no checkpointer, so
    ``aget_state`` cannot be used here.
    """
    final_state: dict[str, Any] = dict(state)
    async for step in graph.astream(state, stream_mode="updates"):
        for node_name, update in step.items():
            if update:
                final_state.update(update)
            yield ("status", {"node": node_name, "update": _slim(update or {})})

    yield ("done", final_state)


def _slim(update: dict) -> dict:
    """Trim large fields (evidence content) from status payloads."""
    slim = {}
    for key, value in update.items():
        if key == "evidence_groups":
            slim[key] = [
                {
                    "task_id": g.get("task_id"),
                    "count": len(g.get("evidence", [])),
                    "status": g.get("status"),
                }
                for g in value
            ]
        elif key in ("final_answer",):
            slim[key] = f"<{len(value or '')} chars>"
        else:
            slim[key] = value
    return slim

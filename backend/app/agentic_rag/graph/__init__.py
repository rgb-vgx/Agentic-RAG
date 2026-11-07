"""LangGraph package.

Heavy imports (langgraph itself) are deferred so importing lightweight
modules like `nodes` does not require the full dependency chain.
"""

from typing import Any

__all__ = ["NodeContext", "build_graph", "initial_state", "stream_run"]


def __getattr__(name: str) -> Any:
    if name == "NodeContext":
        from app.agentic_rag.graph.nodes import NodeContext

        return NodeContext
    if name in ("build_graph", "initial_state", "stream_run"):
        from app.agentic_rag import graph as _graph

        workflow = _graph.__dict__.get("_workflow")
        if workflow is None:
            import app.agentic_rag.graph.workflow as workflow

            _graph._workflow = workflow
        return getattr(workflow, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Langfuse tracing (Section 14.1).

No-op when LANGFUSE_ENABLED=false so the app runs without observability
infrastructure. Every agent node wraps its work in `trace_node` to record
latency, tokens and prompt versions.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.config import settings

_client = None


def get_langfuse():
    global _client
    if not settings.langfuse_enabled:
        return None
    if _client is None:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return _client


@asynccontextmanager
async def trace_node(
    name: str,
    *,
    run_id: str | None = None,
    input: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Context manager yielding a mutable span dict; records on exit."""
    client = get_langfuse()
    span = {"name": name, "output": None, "metadata": metadata or {}}

    if client is None:
        yield span
        return

    import time

    start = time.monotonic()
    try:
        yield span
    finally:
        try:
            client.trace(
                name=name,
                input=input or {},
                output=span.get("output"),
                metadata={**span["metadata"], "run_id": run_id},
                session_id=run_id,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception:
            pass  # observability must never break the request path


def flush() -> None:
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            pass

"""Reranker factory — optional cross-encoder scoring service."""

from app.config import settings


def build_reranker():
    """Return a reranker client, or None when reranking is disabled."""
    if not settings.rerank_enabled or not settings.rerank_url:
        return None
    from app.infrastructure.rerankers.service import HttpReranker

    return HttpReranker(
        url=settings.rerank_url,
        model=settings.rerank_model,
        path=settings.rerank_path,
        timeout=settings.rerank_timeout_seconds,
        instruction=settings.rerank_instruction,
    )

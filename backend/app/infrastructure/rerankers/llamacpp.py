"""llama.cpp reranker client (Qwen3-Reranker via `llama-server --reranking`)."""

from typing import Any

import httpx


class LlamaCppReranker:
    """Scores (query, document) pairs through llama.cpp's /rerank endpoint."""

    def __init__(
        self,
        url: str,
        model: str,
        path: str = "/rerank",
        timeout: float = 120.0,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.path = path if path.startswith("/") else f"/{path}"
        self.timeout = timeout

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return one score per input document, in input order."""
        if not documents:
            return []
        payload = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.url}{self.path}", json=payload)
            response.raise_for_status()
            return self._parse_scores(response.json(), len(documents))

    @staticmethod
    def _parse_scores(body: Any, count: int) -> list[float]:
        """Accept the common rerank response shapes.

        llama.cpp returns {"results": [{"index": i, "relevance_score": s}]}.
        Cohere-style responses use the same field names; some servers return
        "score" instead. Missing indices keep a neutral score.
        """
        results = body.get("results") if isinstance(body, dict) else body
        if not results:
            return [0.0] * count
        scores = [0.0] * count
        for position, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            index = item.get("index", position)
            score = item.get("relevance_score", item.get("score", 0.0))
            if isinstance(index, int) and 0 <= index < count:
                scores[index] = float(score)
        return scores

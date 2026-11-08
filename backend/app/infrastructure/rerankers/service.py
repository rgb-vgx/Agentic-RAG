"""HTTP reranker client for the Qwen3-Reranker service (`/rerank`)."""

from typing import Any

import httpx


class HttpReranker:
    """Sends (query, documents) to the reranker service and returns scores."""

    def __init__(
        self,
        url: str,
        model: str,
        path: str = "/rerank",
        timeout: float = 300.0,
        instruction: str = "",
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.path = path if path.startswith("/") else f"/{path}"
        self.timeout = timeout
        self.instruction = instruction

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Return one [0, 1] score per document, in input order."""
        if not documents:
            return []
        payload: dict[str, Any] = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        if self.instruction:
            payload["instruction"] = self.instruction

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.url}{self.path}", json=payload)
            response.raise_for_status()
            return self._parse_scores(response.json(), len(documents))

    @staticmethod
    def _parse_scores(body: Any, count: int) -> list[float]:
        """Map the service's `{results: [{index, relevance_score}]}` response."""
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

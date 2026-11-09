"""Tavily web search client (server-side only; the key never reaches the browser)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    content: str
    score: float = 0.0


async def search(
    api_key: str,
    query: str,
    *,
    max_results: int = 5,
    timeout: float = 20.0,
) -> list[WebResult]:
    payload = {
        "api_key": api_key,
        "query": query[:400],
        "search_depth": "basic",
        "include_answer": False,
        "max_results": max(1, min(int(max_results), 10)),
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(TAVILY_SEARCH_URL, json=payload)
        response.raise_for_status()
        data = response.json()
    results: list[WebResult] = []
    for item in data.get("results", []) or []:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        results.append(
            WebResult(
                title=str(item.get("title") or url),
                url=url,
                content=str(item.get("content") or "").strip(),
                score=float(item.get("score") or 0.0),
            )
        )
    return results


async def test_key(api_key: str) -> bool:
    results = await search(api_key, "test", max_results=1, timeout=10.0)
    return isinstance(results, list)

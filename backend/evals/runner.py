"""Small legal RAG evaluation runner.

Usage:
    uv run python -m evals.runner \
      --base-url http://localhost:8090 \
      --token "$RAG_ACCESS_TOKEN"

The token is a normal access JWT. Without one, start the API with
AUTH_DISABLED=true for a local smoke run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

CASES = Path(__file__).with_name("legal_cases.json")
FALLBACK_MARKERS = (
    "không cung cấp đủ bằng chứng",
    "không đủ bằng chứng",
    "does not provide enough evidence",
)


def score_case(
    case: dict,
    *,
    answer: str,
    cited_document_ids: list[str],
    document_ids_by_name: dict[str, str],
    latency_ms: int,
) -> dict:
    expected_ids = {
        document_ids_by_name[name]
        for name in case.get("expected_documents", [])
        if name in document_ids_by_name
    }
    cited = set(cited_document_ids)
    citation_hit = bool(expected_ids & cited) if expected_ids else bool(cited)
    terms = [term.lower() for term in case.get("required_terms", [])]
    term_hit = all(term in answer.lower() for term in terms) if terms else True
    fallback = any(marker in answer.lower() for marker in FALLBACK_MARKERS)
    latency_ok = latency_ms <= int(case.get("max_latency_ms", 60000))
    return {
        "id": case.get("id"),
        "passed": citation_hit and term_hit and not fallback and latency_ok,
        "citation_hit": citation_hit,
        "term_hit": term_hit,
        "fallback": fallback,
        "latency_ok": latency_ok,
        "latency_ms": latency_ms,
        "cited_document_ids": sorted(cited),
    }


async def _documents(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> tuple[str, dict[str, str]]:
    response = await client.get("/api/v1/documents", headers=headers)
    response.raise_for_status()
    data = response.json()
    by_name = {item["filename"]: item["id"] for item in data.get("items", [])}
    return data.get("items", []), by_name


async def _create_conversation(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> str:
    response = await client.post(
        "/api/v1/conversations", headers=headers, json={}
    )
    response.raise_for_status()
    return response.json()["id"]


async def run_case(
    client: httpx.AsyncClient, headers: dict[str, str], case: dict
) -> dict:
    conversation_id = await _create_conversation(client, headers)
    answer: list[str] = []
    cited_document_ids: list[str] = []
    started = time.perf_counter()
    event = ""
    async with client.stream(
        "POST",
        "/api/v1/chat",
        headers=headers,
        json={
            "conversation_id": conversation_id,
            "query": case["question"],
        },
        timeout=120,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if event == "token":
                    answer.append(payload.get("delta", ""))
                elif event == "citation":
                    document_id = payload.get("document_id")
                    if document_id:
                        cited_document_ids.append(str(document_id))
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "answer": "".join(answer),
        "cited_document_ids": cited_document_ids,
        "latency_ms": latency_ms,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8090")
    parser.add_argument("--token", default="")
    parser.add_argument("--cases", default=str(CASES))
    args = parser.parse_args()
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    cases = json.loads(Path(args.cases).read_text())

    async with httpx.AsyncClient(base_url=args.base_url) as client:
        _, by_name = await _documents(client, headers)
        results = []
        for case in cases:
            result = await run_case(client, headers, case)
            scored = score_case(
                case,
                answer=result["answer"],
                cited_document_ids=result["cited_document_ids"],
                document_ids_by_name=by_name,
                latency_ms=result["latency_ms"],
            )
            scored["answer"] = result["answer"][:500]
            results.append(scored)
            print(json.dumps(scored, ensure_ascii=False))

    passed = sum(1 for item in results if item["passed"])
    print(
        json.dumps(
            {"passed": passed, "total": len(results), "results": results},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

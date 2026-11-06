"""LLM provider abstraction (Section 6.4, 16.3).

Minimal interface covering the two shapes the agentic pipeline needs:
  - complete(): one-shot chat completion (analysis, evaluation, synthesis)
  - stream(): token streaming for SSE responses
Both return provider-agnostic models so nodes never touch SDK types.
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field


class LLMMessage(BaseModel):
    role: str  # system | user | assistant
    content: str


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    content: str
    model: str
    provider: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
    raw: dict[str, Any] = Field(default_factory=dict)


class BaseLLMClient(ABC):
    """Provider-agnostic chat interface."""

    provider: str = "base"

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        """One-shot completion. temperature=0 by default for determinism."""

    @abstractmethod
    def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Yield content deltas for SSE streaming."""
        raise NotImplementedError

    async def complete_json(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> dict:
        """Completion constrained to JSON output, with fallback extraction."""
        response = await self.complete(
            messages, model=model, max_tokens=max_tokens, json_mode=True
        )
        return extract_json(response.content)


def extract_json(text: str) -> dict:
    """Parse JSON from a model response, tolerating code fences."""
    import json

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    return json.loads(stripped[start : end + 1])

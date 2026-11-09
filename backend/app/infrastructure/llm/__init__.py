"""LLM factory — resolve configured provider (Section 16.3).

Clients are imported lazily inside the factory so the app (and the test
suite) can import LLM base types without every provider SDK installed.
"""

from app.infrastructure.llm.base import (
    BaseLLMClient,
    LLMMessage,
    LLMResponse,
    LLMUsage,
    extract_json,
)

__all__ = [
    "BaseLLMClient",
    "LLMMessage",
    "LLMResponse",
    "LLMUsage",
    "extract_json",
    "get_llm_client",
]


def get_llm_client(
    provider: str | None = None,
    model: str | None = None,
    *,
    thinking: bool = True,
) -> BaseLLMClient:
    from app.config import settings

    name = (provider or settings.llm_provider).lower()
    if name == "anthropic":
        from app.infrastructure.llm.anthropic_client import AnthropicClient

        return AnthropicClient(default_model=model, thinking=thinking)
    if name == "openai":
        from app.infrastructure.llm.openai_client import OpenAIClient

        return OpenAIClient(default_model=model)
    raise ValueError(f"Unknown LLM provider: {name}")

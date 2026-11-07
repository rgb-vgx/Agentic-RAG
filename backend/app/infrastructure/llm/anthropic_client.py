"""Anthropic LLM client (anthropic>=0.39 async SDK)."""

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from app.config import settings
from app.infrastructure.llm.base import (
    BaseLLMClient,
    LLMMessage,
    LLMResponse,
    LLMUsage,
)


class AnthropicClient(BaseLLMClient):
    provider = "anthropic"

    def __init__(self, api_key: str | None = None, default_model: str | None = None):
        key = api_key or settings.anthropic_api_key
        token = settings.anthropic_auth_token
        if not key and not token and not settings.anthropic_base_url:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        kwargs: dict = {}
        if token:
            # Proxies like OpenCode expect Authorization: Bearer
            kwargs["auth_token"] = token
        else:
            kwargs["api_key"] = key or "not-needed"
        if settings.anthropic_base_url:
            kwargs["base_url"] = settings.anthropic_base_url
        if settings.anthropic_extra_headers:
            kwargs["default_headers"] = dict(settings.anthropic_extra_headers)
        self._client = AsyncAnthropic(**kwargs)
        self._default_model = default_model or settings.anthropic_chat_model

    @staticmethod
    def _extra_body(temperature: float) -> dict:
        """Fields the SDK 2.x signature no longer exposes.

        `temperature` was removed from the typed params (and `stream`) but is
        still accepted by the Messages API; thinking/effort are proxy-specific.
        """
        body: dict = {"temperature": temperature}
        if settings.anthropic_thinking_type:
            body["thinking"] = {"type": settings.anthropic_thinking_type}
        if settings.anthropic_output_effort:
            body["output_config"] = {"effort": settings.anthropic_output_effort}
        return body

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        conversation = [m.model_dump() for m in messages if m.role != "system"]

        # The Anthropic Messages API has no response_format parameter. The
        # prompts already ask for JSON and complete_json() extracts it.
        response = await self._client.messages.create(
            model=model or self._default_model,
            system=system or None,
            messages=conversation,
            max_tokens=max_tokens,
            extra_body=self._extra_body(temperature),
        )
        content = "".join(
            block.text for block in response.content if block.type == "text"
        )
        return LLMResponse(
            content=content,
            model=response.model,
            provider=self.provider,
            usage=LLMUsage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
                total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            ),
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        conversation = [m.model_dump() for m in messages if m.role != "system"]

        async with self._client.messages.stream(
            model=model or self._default_model,
            system=system or None,
            messages=conversation,
            max_tokens=max_tokens,
            extra_body=self._extra_body(temperature),
        ) as stream:
            async for text in stream.text_stream:
                yield text

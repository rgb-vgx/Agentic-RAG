"""OpenAI LLM client (openai>=1.x async SDK)."""

from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.config import settings
from app.infrastructure.llm.base import (
    BaseLLMClient,
    LLMMessage,
    LLMResponse,
    LLMUsage,
)


class OpenAIClient(BaseLLMClient):
    provider = "openai"

    def __init__(self, api_key: str | None = None, default_model: str | None = None):
        key = api_key or settings.openai_api_key
        if not key and not settings.openai_base_url:
            raise ValueError("OPENAI_API_KEY is not set")
        kwargs: dict = {"api_key": key or "not-needed"}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self._client = AsyncOpenAI(**kwargs)
        self._default_model = default_model or settings.openai_chat_model

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> LLMResponse:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=[m.model_dump() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        choice = response.choices[0]
        usage = response.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            provider=self.provider,
            usage=LLMUsage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
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
        stream = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=[m.model_dump() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

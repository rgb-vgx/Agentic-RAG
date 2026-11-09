"""Conversation title generation with a small, cheap LLM call."""

from __future__ import annotations

import re

from app.infrastructure.llm.base import BaseLLMClient, LLMMessage

_SYSTEM = (
    "Bạn đặt tiêu đề ngắn gọn cho một cuộc hội thoại hỏi đáp tài liệu. "
    "Chỉ trả về đúng một tiêu đề tiếng Việt từ 3 đến 8 từ, không dấu ngoặc "
    "kép, không đánh số, không giải thích, không có dấu chấm cuối."
)
_MAX_TITLE = 80


def sanitize_title(raw: str) -> str:
    """Normalise a model title; empty string means "keep the fallback"."""
    value = (raw or "").strip()
    value = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", value).strip()
    value = value.splitlines()[0].strip() if value else ""
    value = re.sub(r"^(?:tiêu\s*đề|title)\s*[:：-]\s*", "", value, flags=re.IGNORECASE)
    value = value.strip(" \t\"'“”‘’`")
    value = re.sub(r"\s+", " ", value)
    value = value.rstrip(" .,;:!?")
    if len(value) > _MAX_TITLE:
        value = value[:_MAX_TITLE].rsplit(" ", 1)[0].rstrip(" .,;:!?")
    return value


async def generate_title(
    llm: BaseLLMClient,
    first_query: str,
    *,
    model: str | None = None,
) -> str:
    response = await llm.complete(
        [
            LLMMessage(role="system", content=_SYSTEM),
            LLMMessage(
                role="user",
                content=f"Câu hỏi đầu tiên:\n{first_query}\n\nTiêu đề:",
            ),
        ],
        model=model,
        temperature=0.0,
        # The configured compact model still spends tokens before its final
        # text; 32 truncates it to an empty title.
        max_tokens=512,
    )
    return sanitize_title(response.content)

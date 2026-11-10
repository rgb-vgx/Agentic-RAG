"""Conversation context compaction.

Long chats otherwise send every historical message to every query. Keep the
recent turns verbatim and replace older turns with one short summary. This is
the RAG analogue of Deep Agents' summarization middleware.
"""

from __future__ import annotations

from typing import Any

from app.infrastructure.llm.base import BaseLLMClient, LLMMessage

_SUMMARY_PROMPT = (
    "Tóm tắt các lượt hội thoại cũ dưới đây cho một trợ lý hỏi đáp tài liệu. "
    "Giữ lại: câu hỏi đã hỏi, tài liệu/điều khoản đã nhắc, quyết định và ràng "
    "buộc quan trọng. Bỏ qua lời chào, nội dung lặp và suy đoán. Trả về tối đa "
    "8 dòng tiếng Việt, không thêm lời mở đầu."
)


async def compact_context(
    llm: BaseLLMClient,
    messages: list[dict[str, Any]],
    *,
    keep_last: int = 8,
    max_chars: int = 16000,
    timeout_seconds: float = 20.0,
) -> list[dict[str, Any]]:
    if len(messages) <= keep_last:
        return messages
    total_chars = sum(len(str(item.get("content", ""))) for item in messages)
    if total_chars <= max_chars:
        return messages

    older = messages[:-keep_last]
    recent = messages[-keep_last:]
    transcript = "\n".join(
        f"{item.get('role', 'user')}: {str(item.get('content', ''))[:2000]}"
        for item in older
    )
    try:
        response = await llm.complete(
            [
                LLMMessage(role="system", content=_SUMMARY_PROMPT),
                LLMMessage(role="user", content=transcript[:12000]),
            ],
            max_tokens=512,
        )
        summary = response.content.strip()
    except Exception:  # noqa: BLE001 - compaction must never fail a chat
        summary = ""
    if not summary:
        # Deterministic fallback: retain the most recent turns only.
        return recent
    return [
        {
            "role": "system",
            "content": f"Tóm tắt các lượt hội thoại trước:\n{summary}",
        },
        *recent,
    ]

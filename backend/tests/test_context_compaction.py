"""Conversation context compaction."""

import pytest

from app.agentic_rag.context import compact_context
from tests.fakes import FakeLLM


@pytest.mark.asyncio
async def test_short_context_is_unchanged():
    messages = [{"role": "user", "content": "hello"}]
    assert await compact_context(FakeLLM(), messages) == messages


@pytest.mark.asyncio
async def test_long_context_keeps_recent_turns_and_summary():
    messages = [
        {"role": "user", "content": "x" * 3000},
        {"role": "assistant", "content": "y" * 3000},
        *[{"role": "user", "content": f"recent {i}"} for i in range(10)],
    ]
    compacted = await compact_context(
        FakeLLM(), messages, keep_last=3, max_chars=1000
    )
    assert compacted[0]["role"] == "system"
    assert "Tóm tắt" in compacted[0]["content"]
    assert compacted[1:] == messages[-3:]
    assert len(compacted) == 4

"""ChatService wiring: graph runs, SSE frames are produced, result captured."""

from app.agentic_rag import prompts
from app.services.chat_service import ChatService
from tests.fakes import ANSWER, fake_node_context


def test_resolve_query_prompt_formats_with_history():
    """Regression: the JSON block must be escaped for str.format()."""
    text = prompts.RESOLVE_QUERY.template.format(
        history="user: hello\nassistant: hi", query="What about it?"
    )
    assert '"standalone_query"' in text
    assert "What about it?" in text


async def test_chat_service_streams_and_captures_result():
    service = ChatService(fake_node_context())

    events = [
        event
        async for event in service.stream(
            run_id="run-1",
            user_id="user-1",
            tenant_id="tenant-1",
            conversation_id="conversation-1",
            user_query="What is the policy?",
        )
    ]

    names = [event["event"] for event in events]
    assert "status" in names
    assert "token" in names
    assert names[-1] == "completed"
    assert "error" not in names

    assert service.last_result is not None
    assert service.last_result.answer == ANSWER
    assert service.last_result.routing_path == "fast"
    assert service.last_result.iterations == 1
    assert service.last_result.citations == []

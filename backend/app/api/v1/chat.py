"""Chat endpoints — SSE streaming of agent runs (Section 12).

Event contract (mirrored by the frontend):

    event: run_started   data: {"run_id": "..."}
    event: status        data: {"node": "plan", "message": "..."}
    event: token         data: {"delta": "..."}
    event: citation      data: {"reference_id": "...", "chunk_id": "...", ...}
    event: completed     data: {"run_id": "...", "usage": {...}}
    event: error         data: {"message": "..."}
"""

from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: UUID
    query: str = Field(min_length=1, max_length=8000)
    knowledge_base_id: UUID | None = None
    use_agentic: bool | None = None  # None → server-side complexity routing


class CitationEvent(BaseModel):
    reference_id: str
    chunk_id: UUID
    document_id: UUID
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    snippet: str = ""


@router.post("")
async def chat(request: ChatRequest) -> EventSourceResponse:
    """Run the agentic workflow and stream progress as SSE.

    The service layer resolves conversation context (ResolvedQuery), builds
    the LangGraph state and forwards graph events as SSE frames. Until the
    workflow is wired the endpoint reports a single error event rather than
    hanging, so clients fail fast.
    """

    async def event_stream():
        run_id = str(uuid4())
        yield {"event": "run_started", "data": f'{{"run_id": "{run_id}"}}'}
        yield {
            "event": "error",
            "data": '{"message": "Agent workflow not wired yet"}',
        }

    return EventSourceResponse(event_stream())

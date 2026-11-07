"""Chat endpoints — SSE streaming of agent runs (Section 12).

Event contract (mirrored by the frontend):

    event: run_started   data: {"run_id": "...", "snapshot_document_ids": [...]}
    event: status        data: {"node": "plan", "message": "..."}
    event: token         data: {"delta": "..."}
    event: citation      data: {"reference_id": "...", "chunk_id": "...", ...}
    event: completed     data: {"run_id": "...", "usage": {...}}
    event: error         data: {"message": "..."}

Security: conversation ownership is verified BEFORE a run is created (no
tokens spent on a request that will be denied), the retrieval snapshot is
computed from the caller's visible documents only, and client-provided
document_ids can only narrow that snapshot — never widen it.
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import Principal, get_principal
from app.infrastructure.database.repositories import (
    AgentRunRepository,
    ConversationRepository,
    DocumentRepository,
)
from app.infrastructure.database.session import get_session

router = APIRouter()


def not_found() -> HTTPException:
    # Same message for "does not exist" and "not yours to see".
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")   # no tenant_id/owner_id smuggling

    conversation_id: UUID
    query: str = Field(min_length=1, max_length=8000)
    knowledge_base_id: UUID | None = None
    use_agentic: bool | None = None  # None → server-side complexity routing
    # client-selected documents: only ever intersected with what the user
    # can already see, never used to widen access
    document_ids: list[UUID] | None = None


class CitationEvent(BaseModel):
    reference_id: str
    chunk_id: UUID
    document_id: UUID
    page_start: int | None = None
    page_end: int | None = None


@router.post("")
async def chat(
    body: ChatRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> EventSourceResponse:
    """Run the agentic workflow and stream progress as SSE.

    All database work happens BEFORE the stream starts, so no session is
    held open across the SSE connection. Until the workflow is wired the
    endpoint reports a single error event rather than hanging, so clients
    fail fast.
    """
    conversations = ConversationRepository(session, principal)
    documents = DocumentRepository(session, principal)
    runs = AgentRunRepository(session, principal)

    # 1) ownership check BEFORE creating a run
    conversation = await conversations.get(body.conversation_id)
    if conversation is None:
        raise not_found()

    # 2) snapshot computed per (user, run); document_ids only narrow it
    snapshot = await documents.active_chunk_sets(body.document_ids)
    run = await runs.create(conversation.id, query=body.query, snapshot=snapshot)
    if run is None:
        raise not_found()

    # 3) persist the user's message (ownership already verified above)
    await conversations.add_message(conversation.id, "user", body.query)

    run_id = str(run.id)
    snapshot_document_ids = sorted(str(k) for k in snapshot)

    async def event_stream():
        yield {
            "event": "run_started",
            "data": json.dumps(
                {"run_id": run_id, "snapshot_document_ids": snapshot_document_ids}
            ),
        }
        yield {
            "event": "error",
            "data": json.dumps({"message": "Agent workflow not wired yet"}),
        }

    return EventSourceResponse(event_stream())

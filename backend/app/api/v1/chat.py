"""Chat endpoints — SSE streaming of agent runs (Section 12).

Event contract (mirrored by the frontend):

    event: run_started   data: {"run_id": "...", "model": "...",
                                "model_provider": "...",
                                "snapshot_document_ids": [...]}
    event: status        data: {"node": "plan", "update": {...}}
    event: token         data: {"delta": "..."}
    event: citation      data: {"reference_id": "...", "chunk_id": "...", ...}
    event: completed     data: {"run_id": "...", "routing_path": "...",
                                "iterations": 1, "citations": 2,
                                "latency_ms": 1234}
    event: error         data: {"message": "..."}

Security: conversation ownership is verified BEFORE a run is created (no
tokens spent on a request that will be denied), the retrieval snapshot is
computed from the caller's visible documents only, and client-provided
document_ids can only narrow that snapshot — never widen it. The selected
model must be in the server-side allowlist (`conf.json`).

Database sessions are short-lived: one for the pre-stream checks, one to
create the run/message, and one to persist the result. No session is held
open for the lifetime of the SSE response.
"""

import asyncio
import json
import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from app.agentic_rag.graph.nodes import NodeContext
from app.auth import Principal, get_principal
from app.config import settings
from app.integrations import tavily
from app.infrastructure.database.repositories import (
    AgentRunRepository,
    ConversationRepository,
    DocumentRepository,
)
from app.infrastructure.database.session import get_session_factory
from app.services.chat_service import ChatRunResult, ChatService
from app.services.integration_settings import IntegrationSettingsService

router = APIRouter()
logger = logging.getLogger(__name__)

# Keep finalization tasks alive after a client disconnect. A task stored only
# in the cancelled request's frame can be garbage-collected before it writes.
_persist_tasks: set[asyncio.Task] = set()


def _track_persist(task: asyncio.Task) -> None:
    _persist_tasks.add(task)
    task.add_done_callback(_persist_tasks.discard)
    task.add_done_callback(
        lambda done: (
            logger.exception("failed to finalize chat run", exc_info=done.exception())
            if not done.cancelled() and done.exception()
            else None
        )
    )


def not_found() -> HTTPException:
    # Same message for "does not exist" and "not yours to see".
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")   # no tenant_id/owner_id smuggling

    conversation_id: UUID
    query: str = Field(min_length=1, max_length=8000)
    knowledge_base_id: UUID | None = None
    use_agentic: bool | None = None  # None → server-side complexity routing
    # selected model; must be in the server-side allowlist
    model: str | None = Field(default=None, min_length=1, max_length=128)
    # client-selected documents: only ever intersected with what the user
    # can already see, never used to widen access
    document_ids: list[UUID] | None = None
    # Per-user Tavily toggle; requires a configured integration key.
    web_search: bool = False


class CitationEvent(BaseModel):
    reference_id: str
    chunk_id: str
    document_id: UUID
    page_start: int | None = None
    page_end: int | None = None
    url: str | None = None


@router.post("")
async def chat(
    request: Request,
    body: ChatRequest,
    principal: Annotated[Principal, Depends(get_principal)],
) -> EventSourceResponse:
    """Run the agentic workflow and stream progress as SSE."""
    session_factory = get_session_factory()

    # 1) Authorization, model allowlist, history and snapshot.
    async with session_factory() as session:
        conversations = ConversationRepository(session, principal)
        documents = DocumentRepository(session, principal)

        conversation = await conversations.get(body.conversation_id)
        if conversation is None:
            raise not_found()
        conversation_id = conversation.id

        selected = settings.default_model_option()
        if body.model is not None:
            requested = next(
                (o for o in settings.available_models() if o.id == body.model),
                None,
            )
            if requested is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported model")
            selected = requested

        history = await conversations.messages(conversation_id) or []
        conversation_context = [
            {"role": message.role, "content": message.content}
            for message in history
        ]
        snapshot = await documents.active_chunk_sets(body.document_ids)
        document_metadata = await documents.retrieval_metadata(
            list(snapshot.keys())
        )

    web_evidence: list[dict] = []
    if body.web_search:
        async with session_factory() as session:
            api_key = await IntegrationSettingsService(
                session, principal
            ).get_key("tavily")
        if not api_key:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Tavily chưa được cấu hình trong Settings",
            )
        try:
            results = await asyncio.wait_for(
                tavily.search(api_key, body.query), timeout=25
            )
        except Exception as exc:  # noqa: BLE001 - external search failure
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Tavily search failed: {exc}",
            ) from exc
        for index, item in enumerate(results[:5], start=1):
            web_evidence.append(
                {
                    "chunk_id": f"web-{uuid4()}",
                    "document_id": str(uuid4()),
                    "content": f"{item.title}\n{item.content}",
                    "score": item.score,
                    "page_start": None,
                    "page_end": None,
                    "heading_path": [item.title],
                    "metadata": {
                        "url": item.url,
                        "title": item.title,
                        "source": "web",
                    },
                }
            )

    # 2) Build dependencies before writing a run row: a misconfigured
    #    provider must not leave an orphaned "running" run behind.
    factory = request.app.state.chat_context_factory
    try:
        ctx: NodeContext = factory(selected.provider, selected.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    ctx.documents = document_metadata

    # 3) Create the run and persist the user's message.
    async with session_factory() as session:
        conversations = ConversationRepository(session, principal)
        runs = AgentRunRepository(session, principal)
        run = await runs.create(
            conversation_id,
            query=body.query,
            snapshot=snapshot,
            model_provider=selected.provider,
            model_name=selected.id,
        )
        if run is None:
            raise not_found()
        run_id = run.id
        await conversations.add_message(conversation_id, "user", body.query)

    service = ChatService(
        ctx, checkpointer=getattr(request.app.state, "checkpointer", None)
    )
    snapshot_document_ids = sorted(str(key) for key in snapshot)

    async def persist(
        *,
        outcome: str,
        result: ChatRunResult | None,
        error: str | None,
    ) -> None:
        async with session_factory() as session:
            runs = AgentRunRepository(session, principal)
            conversations = ConversationRepository(session, principal)
            if result is not None:
                await conversations.add_message(
                    conversation_id,
                    "assistant",
                    result.answer,
                    sources=result.citations,
                    model_name=selected.id,
                )
            await runs.update(
                run_id,
                status=outcome,
                final_answer=result.answer if result else None,
                citations=result.citations if result else None,
                routing_path=result.routing_path if result else None,
                retrieval_iterations=result.iterations if result else None,
                latency_ms=result.latency_ms if result else None,
                error_message=error,
            )

    async def event_stream():
        yield {
            "event": "run_started",
            "data": json.dumps(
                {
                    "run_id": str(run_id),
                    "model": selected.id,
                    "model_provider": selected.provider,
                    "snapshot_document_ids": snapshot_document_ids,
                }
            ),
        }

        finalized = False
        try:
            async for event in service.stream(
                run_id=str(run_id),
                user_id=str(principal.user_id),
                tenant_id=str(principal.tenant_id),
                conversation_id=str(conversation_id),
                user_query=body.query,
                conversation_context=conversation_context,
                document_ids=snapshot_document_ids,
                web_evidence=web_evidence,
            ):
                if event["event"] in ("completed", "error"):
                    try:
                        await persist(
                            outcome=(
                                "completed"
                                if event["event"] == "completed"
                                else "failed"
                            ),
                            result=service.last_result,
                            error=service.last_error,
                        )
                    except Exception:  # persistence must not break the stream
                        logger.exception("failed to persist chat run %s", run_id)
                    finalized = True
                yield event
        finally:
            if not finalized:
                # Shield the write: on a client/Cloudflare disconnect this
                # generator is already being cancelled, and an unshielded
                # await would be cancelled before the run leaves "running".
                task = asyncio.create_task(
                    persist(
                        outcome="failed",
                        result=None,
                        error=service.last_error or "client disconnected",
                    )
                )
                _track_persist(task)
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    logger.warning(
                        "chat client disconnected; finalizing run %s in background",
                        run_id,
                    )
                    raise

    return EventSourceResponse(event_stream())

"""Conversation and message history endpoints.

Every route is private per (tenant, user): a conversation that belongs to
someone else — even in the same tenant — is reported as 404, identical to
one that does not exist (no existence oracle).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_principal
from app.config import settings
from app.infrastructure.database.models import MessageRecord
from app.infrastructure.database.repositories import ConversationRepository
from app.infrastructure.database.session import get_session
from app.infrastructure.llm import get_llm_client
from app.services.title_service import generate_title

router = APIRouter()


def not_found() -> HTTPException:
    # Same message for "does not exist" and "not yours to see".
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")   # no tenant_id/owner_id smuggling

    title: str = Field(default="New conversation", min_length=1, max_length=200)


class ConversationRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)


class AutoTitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, min_length=1, max_length=128)


class ConversationResponse(BaseModel):
    id: UUID
    title: str
    title_source: str = "first_message"
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_record(cls, record) -> "ConversationResponse":
        return cls(
            id=record.id,
            title=record.title,
            title_source=getattr(record, "title_source", "first_message"),
            created_at=record.created_at.isoformat() if record.created_at else None,
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
        )


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    sources: list[dict] = Field(default_factory=list)
    model_name: str | None = None
    created_at: str | None = None

    @classmethod
    def from_record(cls, record: MessageRecord) -> "MessageResponse":
        return cls(
            id=record.id,
            role=record.role,
            content=record.content,
            sources=list(record.sources or []),
            model_name=record.model_name,
            created_at=record.created_at.isoformat() if record.created_at else None,
        )


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse] = Field(default_factory=list)
    total: int = 0


def _repo(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> ConversationRepository:
    return ConversationRepository(session, principal)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    repo: ConversationRepository = Depends(_repo),
) -> ConversationListResponse:
    records = await repo.list()
    return ConversationListResponse(
        items=[ConversationResponse.from_record(r) for r in records],
        total=len(records),
    )


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    body: ConversationCreate, repo: ConversationRepository = Depends(_repo)
) -> ConversationResponse:
    record = await repo.create(body.title)
    return ConversationResponse.from_record(record)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID, repo: ConversationRepository = Depends(_repo)
) -> ConversationResponse:
    record = await repo.get(conversation_id)
    if record is None:
        raise not_found()
    return ConversationResponse.from_record(record)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    conversation_id: UUID,
    body: ConversationRename,
    repo: ConversationRepository = Depends(_repo),
) -> ConversationResponse:
    title = body.title.strip()
    if not title:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Title must not be blank"
        )
    record = await repo.rename(conversation_id, title)
    if record is None:
        raise not_found()
    return ConversationResponse.from_record(record)


@router.post("/{conversation_id}/auto-title", response_model=ConversationResponse)
async def auto_title_conversation(
    conversation_id: UUID,
    body: AutoTitleRequest,
    repo: ConversationRepository = Depends(_repo),
) -> ConversationResponse:
    record = await repo.get(conversation_id)
    if record is None:
        raise not_found()
    # A manual rename always wins; an auto title is generated only once.
    if record.title_source in {"manual", "auto"}:
        return ConversationResponse.from_record(record)

    messages = await repo.messages(conversation_id) or []
    first_query = next(
        (message.content for message in messages if message.role == "user"),
        None,
    )
    if not first_query:
        return ConversationResponse.from_record(record)

    option = next(
        (item for item in settings.available_models() if item.id == body.model),
        settings.default_model_option(),
    )
    if body.model is not None and option.id != body.model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported model")
    try:
        llm = get_llm_client(option.provider, model=option.id, thinking=False)
        title = await generate_title(llm, first_query[:4000], model=option.id)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - title failure must not break chat
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Title generation failed: {exc}"
        ) from exc
    if not title:
        return ConversationResponse.from_record(record)
    updated = await repo.set_title(
        conversation_id,
        title,
        source="auto",
        only_if_source="first_message",
    )
    return ConversationResponse.from_record(updated or record)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: UUID, repo: ConversationRepository = Depends(_repo)
) -> list[MessageResponse]:
    records = await repo.messages(conversation_id)
    if records is None:
        raise not_found()
    return [MessageResponse.from_record(r) for r in records]


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID, repo: ConversationRepository = Depends(_repo)
) -> None:
    if not await repo.delete(conversation_id):
        raise not_found()

"""Conversation and message history endpoints."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.domain.conversations.entities import Conversation, Message

router = APIRouter()


class ConversationResponse(BaseModel):
    id: UUID
    title: str
    created_at: str | None = None
    updated_at: str | None = None


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    sources: list[dict] = Field(default_factory=list)
    model_name: str | None = None
    created_at: str | None = None

    @classmethod
    def from_message(cls, message: Message) -> "MessageResponse":
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            sources=[source.model_dump(mode="json") for source in message.sources],
            model_name=message.model_name,
            created_at=message.created_at,
        )


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse] = Field(default_factory=list)
    total: int = 0


@router.get("", response_model=ConversationListResponse)
async def list_conversations() -> ConversationListResponse:
    raise HTTPException(status_code=501, detail="Conversation repository not wired yet")


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(title: str = "New conversation") -> ConversationResponse:
    raise HTTPException(status_code=501, detail="Conversation repository not wired yet")


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: UUID) -> ConversationResponse:
    raise HTTPException(status_code=501, detail="Conversation repository not wired yet")


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(conversation_id: UUID) -> list[MessageResponse]:
    raise HTTPException(status_code=501, detail="Conversation repository not wired yet")


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: UUID) -> None:
    raise HTTPException(status_code=501, detail="Conversation repository not wired yet")

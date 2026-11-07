"""Conversation domain entities."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class SourceReference(BaseModel):
    """A validated citation pointing back to a retrieved chunk."""

    reference_id: str
    chunk_id: str
    document_id: UUID
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    snippet: str = ""


class Message(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    conversation_id: UUID
    role: MessageRole
    content: str
    sources: list[SourceReference] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    model_name: str | None = None
    created_at: str | None = None


class Conversation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    tenant_id: str
    user_id: UUID
    title: str = "New conversation"
    created_at: str | None = None
    updated_at: str | None = None

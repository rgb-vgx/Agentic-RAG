"""v1 API router aggregation."""

from fastapi import APIRouter

from app.api.v1 import chat, conversations, documents, health

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])

"""v1 API router aggregation.

Deny-by-default: only health probes and the Google token exchange are
public. Everything else sits behind get_principal at the router level, so
a newly added route is protected unless someone deliberately adds it to
public_router (guard test: tests/integration/test_auth.py).
"""

from fastapi import APIRouter, Depends

from app.api.v1 import auth, chat, conversations, documents, health, models
from app.auth import get_principal

public_router = APIRouter()
public_router.include_router(health.router, tags=["health"])
public_router.include_router(auth.router, tags=["auth"])

protected_router = APIRouter(dependencies=[Depends(get_principal)])
protected_router.include_router(models.router, prefix="/models", tags=["models"])
protected_router.include_router(
    documents.router, prefix="/documents", tags=["documents"]
)
protected_router.include_router(
    conversations.router, prefix="/conversations", tags=["conversations"]
)
protected_router.include_router(chat.router, prefix="/chat", tags=["chat"])

api_router = APIRouter()
api_router.include_router(public_router)
api_router.include_router(protected_router)

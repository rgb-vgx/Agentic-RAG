"""Model catalogue — the server-side allowlist for chat model selection.

The client may only pick from this list; the backend never trusts an
arbitrary model id from the request.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter()


class ModelOption(BaseModel):
    id: str
    label: str
    provider: str


class ModelListResponse(BaseModel):
    items: list[ModelOption]
    default: str


@router.get("", response_model=ModelListResponse)
async def list_models() -> ModelListResponse:
    options = settings.available_models()
    return ModelListResponse(
        items=[
            ModelOption(id=o.id, label=o.label or o.id, provider=o.provider)
            for o in options
        ],
        default=settings.default_model_option().id,
    )

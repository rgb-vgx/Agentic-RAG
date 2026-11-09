"""Per-user integration settings (Tavily web search)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_principal
from app.infrastructure.database.session import get_session
from app.integrations import tavily
from app.services.integration_settings import (
    IntegrationSettingsService,
    decrypt_secret,
    mask_secret,
)

router = APIRouter()


class TavilyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None, min_length=1, max_length=256)
    enabled: bool = True


class TavilyTest(BaseModel):
    api_key: str | None = Field(default=None, min_length=1, max_length=256)


class TavilyStatus(BaseModel):
    configured: bool
    enabled: bool
    masked_key: str | None = None
    updated_at: str | None = None


def _service(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> IntegrationSettingsService:
    return IntegrationSettingsService(session, principal)


def _status(record) -> TavilyStatus:
    if record is None:
        return TavilyStatus(configured=False, enabled=False)
    try:
        masked = mask_secret(decrypt_secret(record.encrypted_key))
    except ValueError:
        masked = None
    return TavilyStatus(
        configured=True,
        enabled=record.enabled,
        masked_key=masked,
        updated_at=record.updated_at.isoformat() if record.updated_at else None,
    )


@router.get("/tavily", response_model=TavilyStatus)
async def get_tavily(
    service: IntegrationSettingsService = Depends(_service),
) -> TavilyStatus:
    return _status(await service.get("tavily"))


@router.put("/tavily", response_model=TavilyStatus)
async def put_tavily(
    body: TavilyUpdate,
    service: IntegrationSettingsService = Depends(_service),
) -> TavilyStatus:
    record = await service.get("tavily")
    if record is None and not body.api_key:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "api_key is required the first time",
        )
    try:
        record = await service.upsert(
            "tavily", body.api_key.strip() if body.api_key else None,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _status(record)


@router.delete("/tavily", status_code=204)
async def delete_tavily(
    service: IntegrationSettingsService = Depends(_service),
) -> None:
    if not await service.delete("tavily"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not configured")


@router.post("/tavily/test")
async def test_tavily(
    body: TavilyTest,
    service: IntegrationSettingsService = Depends(_service),
) -> dict:
    api_key = (body.api_key or "").strip() or await service.get_key("tavily")
    if not api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No Tavily key configured")
    try:
        await tavily.test_key(api_key)
    except Exception as exc:  # noqa: BLE001 - expose a compact validation error
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Tavily rejected the key: {exc}"
        ) from exc
    return {"ok": True}

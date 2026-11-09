"""Tenant-scoped document domain endpoints.

Built-in domains are global and read-only. Custom domains belong to the
caller's tenant; another tenant's custom slug is reported as 404, exactly
like a slug that does not exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_principal
from app.domain.documents.domains import (
    BUILTIN_DOMAINS,
    DomainDefinition,
    domain_options,
    is_valid_domain_slug,
    normalize_domain_slug,
)
from app.infrastructure.database.models import DocumentDomainRecord
from app.infrastructure.database.repositories import DocumentDomainRepository
from app.infrastructure.database.session import get_session

router = APIRouter()

MAX_CUSTOM_DOMAINS = 50


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


class DomainCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=256)
    slug: str | None = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=2000)
    hints: list[str] | None = None


class DomainUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    hints: list[str] | None = None


class DomainResponse(BaseModel):
    slug: str
    label: str
    description: str = ""
    is_builtin: bool
    hints: list[str] = Field(default_factory=list)

    @classmethod
    def from_definition(cls, definition: DomainDefinition) -> DomainResponse:
        return cls(
            slug=definition.slug,
            label=definition.label,
            description=definition.description,
            is_builtin=definition.is_builtin,
            hints=list(definition.hints),
        )


class DomainListResponse(BaseModel):
    items: list[DomainResponse] = Field(default_factory=list)
    total: int = 0


def _clean_hints(hints: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for hint in hints or []:
        text = str(hint).strip()[:200]
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= 20:
            break
    return cleaned


def _definition(record: DocumentDomainRecord) -> DomainDefinition:
    return DomainDefinition(
        slug=record.slug,
        label=record.label,
        description=record.description or "",
        hints=tuple(record.hints or ()),
        is_builtin=False,
        tenant_id=record.tenant_id,
    )


async def available_domains(
    session: AsyncSession, principal: Principal
) -> list[DomainDefinition]:
    """Built-ins followed by the caller tenant's custom domains."""
    repository = DocumentDomainRepository(session, principal)
    records = await repository.list_custom()
    return domain_options([_definition(record) for record in records])


def _repo(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> DocumentDomainRepository:
    return DocumentDomainRepository(session, principal)


@router.get("", response_model=DomainListResponse)
async def list_domains(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> DomainListResponse:
    options = await available_domains(session, principal)
    return DomainListResponse(
        items=[DomainResponse.from_definition(item) for item in options],
        total=len(options),
    )


@router.post("", response_model=DomainResponse, status_code=201)
async def create_domain(
    body: DomainCreate,
    repo: DocumentDomainRepository = Depends(_repo),
) -> DomainResponse:
    label = body.label.strip()
    if not label:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Label must not be blank"
        )

    slug = normalize_domain_slug(body.slug or label)
    if not is_valid_domain_slug(slug):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid domain slug"
        )
    if slug in BUILTIN_DOMAINS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Domain slug already exists"
        )
    if await repo.get_custom(slug) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Domain slug already exists"
        )
    if await repo.count_custom() >= MAX_CUSTOM_DOMAINS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"At most {MAX_CUSTOM_DOMAINS} custom domains per tenant",
        )

    record = DocumentDomainRecord(
        slug=slug,
        label=label,
        description=(body.description or "").strip(),
        hints=_clean_hints(body.hints),
    )
    try:
        record = await repo.add(record)
    except IntegrityError as exc:
        await repo.session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Domain slug already exists"
        ) from exc
    return DomainResponse.from_definition(_definition(record))


@router.patch("/{slug}", response_model=DomainResponse)
async def update_domain(
    slug: str,
    body: DomainUpdate,
    repo: DocumentDomainRepository = Depends(_repo),
) -> DomainResponse:
    normalized = normalize_domain_slug(slug)
    if normalized in BUILTIN_DOMAINS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Built-in domains cannot be changed"
        )
    record = await repo.get_custom(normalized)
    if record is None:
        raise not_found()

    if body.label is not None:
        label = body.label.strip()
        if not label:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Label must not be blank"
            )
        record.label = label
    if body.description is not None:
        record.description = body.description.strip()
    if body.hints is not None:
        record.hints = _clean_hints(body.hints)
    record = await repo.save(record)
    return DomainResponse.from_definition(_definition(record))


@router.delete("/{slug}", status_code=204)
async def delete_domain(
    slug: str,
    repo: DocumentDomainRepository = Depends(_repo),
) -> None:
    normalized = normalize_domain_slug(slug)
    if normalized in BUILTIN_DOMAINS:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Built-in domains cannot be deleted"
        )
    record = await repo.get_custom(normalized)
    if record is None:
        raise not_found()
    await repo.delete(record)

"""Authentication endpoint — Google ID token exchange (invite-only).

Backend is the source of truth for tenant_id/roles: Google only proves
identity; the tenant and roles come from the memberships table and are
signed into our own access JWT. Unknown google_sub -> 403, never
auto-provisioned (domain auto-join is deferred).

Note: tenant ids are UUID-formatted strings (String(64) column); a tenant
whose id is not UUID-shaped would fail here with a 500 — keep the
convention when seeding tenants.
"""

from dataclasses import replace
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GoogleIdentity, Principal, get_principal, issue_access_token
from app.config import settings
from app.infrastructure.database.models import MembershipRecord, UserRecord
from app.infrastructure.database.repositories import find_member_by_google_sub
from app.infrastructure.database.session import get_session

router = APIRouter()


class GoogleLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")   # client cannot smuggle extra claims

    id_token: str = Field(min_length=1, max_length=8192)


class GoogleCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=8192)
    redirect_uri: str = Field(min_length=1, max_length=1024)
    code_verifier: str = Field(min_length=43, max_length=128)


class GoogleAuthConfig(BaseModel):
    enabled: bool
    client_id: str
    redirect_uri: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    user_id: UUID
    tenant_id: UUID
    email: str
    roles: list[str]
    avatar_url: str | None = None


def _unauthorized() -> HTTPException:
    # One message for every failure: no oracle for the attacker.
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("/auth/me", response_model=MeResponse)
async def me(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    user = await session.get(UserRecord, principal.user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return MeResponse(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        email=user.email,
        roles=sorted(principal.roles),
        avatar_url=user.avatar_url,
    )


@router.get("/auth/google/config", response_model=GoogleAuthConfig)
async def google_config() -> GoogleAuthConfig:
    enabled = bool(settings.google_client_id and settings.google_client_secret)
    return GoogleAuthConfig(
        enabled=enabled,
        client_id=settings.google_client_id,
        redirect_uri=settings.google_redirect_uri,
    )


def _bootstrap_email_allowed(email: str) -> bool:
    allowed = {
        item.strip().lower()
        for item in settings.auth_bootstrap_emails.split(",")
        if item.strip()
    }
    return bool(email) and email.strip().lower() in allowed


async def _bootstrap_member(
    session: AsyncSession, ident: GoogleIdentity
) -> None:
    """Create/repair a membership for an allowlisted verified email."""
    email = ident.email.strip().lower()
    user = (
        await session.execute(
            select(UserRecord).where(func.lower(UserRecord.email) == email)
        )
    ).scalar_one_or_none()
    if user is None:
        user = UserRecord(
            google_sub=ident.sub, email=email, avatar_url=ident.picture or None
        )
        session.add(user)
        await session.flush()
    else:
        user.google_sub = ident.sub
        user.disabled_at = None
        user.avatar_url = ident.picture or user.avatar_url
    membership = await session.get(MembershipRecord, user.id)
    if membership is None:
        session.add(
            MembershipRecord(
                user_id=user.id,
                tenant_id=str(
                    settings.auth_bootstrap_tenant_id or settings.dev_tenant_id
                ),
                roles=["member", "admin"],
            )
        )
    await session.commit()


async def _issue_for_identity(
    request: Request, session: AsyncSession, ident: GoogleIdentity
) -> TokenResponse:
    if not ident.email_verified:
        raise _unauthorized()
    member = await find_member_by_google_sub(session, ident.sub)
    if member is None and _bootstrap_email_allowed(ident.email):
        await _bootstrap_member(session, ident)
        member = await find_member_by_google_sub(session, ident.sub)
    # Invite-only for everyone not on AUTH_BOOTSTRAP_EMAILS.
    if member is None or member["disabled_at"] is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
    if ident.picture:
        await session.execute(
            update(UserRecord)
            .where(UserRecord.id == member["user_id"])
            .values(avatar_url=ident.picture)
        )
        await session.commit()
    s = request.app.state.auth
    token = issue_access_token(
        s,
        user_id=member["user_id"],
        tenant_id=UUID(str(member["tenant_id"])),
        roles=set(member["roles"]),
    )
    return TokenResponse(access_token=token, expires_in=s.access_ttl_seconds)


@router.post("/auth/google", response_model=TokenResponse)
async def auth_google(
    body: GoogleLoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        ident: GoogleIdentity = await request.app.state.google_verifier(body.id_token)
    except ValueError:
        raise _unauthorized() from None
    return await _issue_for_identity(request, session, ident)


@router.post("/auth/google/code", response_model=TokenResponse)
async def auth_google_code(
    body: GoogleCodeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Google OAuth is not configured"
        )
    if body.redirect_uri != settings.google_redirect_uri:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid redirect URI")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": body.code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": body.redirect_uri,
                    "code_verifier": body.code_verifier,
                    "grant_type": "authorization_code",
                },
            )
            if response.status_code != 200:
                raise _unauthorized()
            token_data = response.json()
            id_token = token_data.get("id_token")
            if not id_token:
                raise _unauthorized()
            picture = ""
            access_token = token_data.get("access_token")
            if access_token:
                # The ID token normally carries `picture`; fetch userinfo as
                # a fallback for accounts/tenants where it is omitted.
                try:
                    info = await client.get(
                        "https://www.googleapis.com/oauth2/v3/userinfo",
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    if info.status_code == 200:
                        picture = str(info.json().get("picture") or "")
                except httpx.HTTPError:
                    picture = ""
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Google token exchange failed"
        ) from exc
    try:
        ident: GoogleIdentity = await request.app.state.google_verifier(id_token)
    except ValueError:
        raise _unauthorized() from None
    if picture and not ident.picture:
        ident = replace(ident, picture=picture)
    return await _issue_for_identity(request, session, ident)

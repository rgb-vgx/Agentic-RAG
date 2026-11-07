"""Authentication endpoint — Google ID token exchange (invite-only).

Backend is the source of truth for tenant_id/roles: Google only proves
identity; the tenant and roles come from the memberships table and are
signed into our own access JWT. Unknown google_sub -> 403, never
auto-provisioned (domain auto-join is deferred).

Note: tenant ids are UUID-formatted strings (String(64) column); a tenant
whose id is not UUID-shaped would fail here with a 500 — keep the
convention when seeding tenants.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import GoogleIdentity, issue_access_token
from app.infrastructure.database.repositories import find_member_by_google_sub
from app.infrastructure.database.session import get_session

router = APIRouter()


class GoogleLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")   # client cannot smuggle extra claims

    id_token: str = Field(min_length=1, max_length=8192)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def _unauthorized() -> HTTPException:
    # One message for every failure: no oracle for the attacker.
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


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
    if not ident.email_verified:
        raise _unauthorized()

    member = await find_member_by_google_sub(session, ident.sub)
    # MVP: invite-only. No membership (or disabled) -> 403, no tenant/user creation.
    if member is None or member["disabled_at"] is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    s = request.app.state.auth
    token = issue_access_token(
        s,
        user_id=member["user_id"],
        tenant_id=UUID(str(member["tenant_id"])),
        roles=set(member["roles"]),
    )
    return TokenResponse(access_token=token, expires_in=s.access_ttl_seconds)

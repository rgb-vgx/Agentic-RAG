"""Principal + access JWT.

Ported from the rag_auth package (verified against the reference tests).

Nguyên tắc: backend là nguồn chân lý cho tenant_id/roles. Google chỉ chứng minh danh tính;
tenant và role lấy từ bảng memberships rồi ký vào access JWT của chính hệ thống.

Adaptation: none — Principal.tenant_id stays a UUID claim; SQL boundaries
convert to str where the project convention is String(64).
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

ACCESS_TYP = "access"


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]


@dataclass(frozen=True)
class AuthSettings:
    secret: str
    issuer: str = "rag-api"
    audience: str = "rag-web"
    access_ttl_seconds: int = 900
    leeway_seconds: int = 5
    algorithm: str = "HS256"   # ghim cứng; không bao giờ đọc alg từ header token


class AuthError(Exception):
    pass


def issue_access_token(
    s: AuthSettings, *, user_id: UUID, tenant_id: UUID, roles: frozenset[str] | set[str],
    now: float | None = None,
) -> str:
    now = int(time.time() if now is None else now)
    return jwt.encode(
        {
            "sub": str(user_id), "tid": str(tenant_id), "roles": sorted(roles),
            "iss": s.issuer, "aud": s.audience, "iat": now, "exp": now + s.access_ttl_seconds,
            "typ": ACCESS_TYP, "jti": uuid.uuid4().hex,
        },
        s.secret, algorithm=s.algorithm,
    )


def verify_access_token(s: AuthSettings, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token, s.secret, algorithms=[s.algorithm], audience=s.audience, issuer=s.issuer,
            leeway=s.leeway_seconds,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "tid"]},
        )
        if claims.get("typ") != ACCESS_TYP:
            raise AuthError("wrong token type")
        roles = claims.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise AuthError("bad roles")
        return Principal(UUID(claims["sub"]), UUID(claims["tid"]), frozenset(roles))
    except (jwt.PyJWTError, ValueError, TypeError) as e:   # UUID(...) lỗi -> ValueError
        raise AuthError("invalid token") from e


# ---- thu hồi: user bị khoá / bị xoá khỏi tenant ------------------------------------------
ActiveChecker = Callable[[Principal], Awaitable[bool]]


def cached_active_checker(fetch: ActiveChecker, ttl_seconds: float = 60.0) -> ActiveChecker:
    """Cache trong process. ttl = độ trễ thu hồi tối đa. Nhiều replica -> cân nhắc Redis."""
    cache: dict[tuple[UUID, UUID], tuple[float, bool]] = {}

    async def check(p: Principal) -> bool:
        key, now = (p.user_id, p.tenant_id), time.monotonic()
        hit = cache.get(key)
        if hit and now - hit[0] < ttl_seconds:
            return hit[1]
        ok = await fetch(p)
        cache[key] = (now, ok)
        return ok

    return check


# ---- FastAPI dependencies ----------------------------------------------------------------
def _unauthorized() -> HTTPException:
    # Một thông điệp duy nhất cho mọi lỗi: không cho kẻ tấn công phân biệt nguyên nhân.
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                         headers={"WWW-Authenticate": "Bearer"})


async def get_principal(request: Request) -> Principal:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    try:
        principal = verify_access_token(request.app.state.auth, token)
    except AuthError:
        raise _unauthorized() from None
    checker: ActiveChecker | None = getattr(request.app.state, "active_checker", None)
    if checker is not None and not await checker(principal):
        raise _unauthorized()
    return principal


def require_role(role: str):
    async def dep(p: Principal = Depends(get_principal)) -> Principal:
        if role not in p.roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")
        return p
    return dep


# ---- Google ID token ---------------------------------------------------------------------
@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool


GoogleVerifier = Callable[[str], Awaitable[GoogleIdentity]]   # raise ValueError nếu token xấu


def make_google_verifier(client_id: str) -> GoogleVerifier:
    async def verify(id_token_str: str) -> GoogleIdentity:
        from google.auth.transport import requests as g_requests  # pip install google-auth
        from google.oauth2 import id_token as g_id_token

        def _sync() -> dict:
            # kiểm chữ ký, exp, iss và aud == client_id
            return g_id_token.verify_oauth2_token(id_token_str, g_requests.Request(), client_id)

        info = await run_in_threadpool(_sync)
        return GoogleIdentity(info["sub"], info.get("email", ""), bool(info.get("email_verified")))

    return verify

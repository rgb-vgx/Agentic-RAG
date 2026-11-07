"""Auth: deny-by-default guard, forged tokens, Google exchange, revocation."""
import re
import time
import uuid

import httpx
import jwt

from app.auth import cached_active_checker, issue_access_token
from app.main import _db_is_active, create_app
from tests.integration.conftest import AUTH, FAKE_IDENTITIES, PUBLIC_PATHS


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_every_non_public_route_rejects_anonymous(world):
    """Guard deny-by-default: add a route and forget auth -> this test goes red.

    Routes are enumerated through OpenAPI (stable across FastAPI versions).
    A route marked include_in_schema=False is skipped by this scan: never
    use that flag on a route that needs auth.
    """
    paths = world.app.openapi()["paths"]
    targets = [
        (method.upper(), path_tpl)
        for path_tpl, ops in paths.items()
        if path_tpl not in PUBLIC_PATHS
        for method in ops
    ]
    assert len(targets) >= 10  # anti-vacuous: guard against filtering the wrong paths

    async with world.client() as c:
        for method, path_tpl in targets:
            path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path_tpl)
            resp = await c.request(
                method, path, json={} if method in {"POST", "PUT", "PATCH"} else None
            )
            assert resp.status_code == 401, f"{method} {path_tpl} -> {resp.status_code}"
            assert resp.json()["detail"] == "Not authenticated"


async def test_public_paths_are_reachable_without_token(world):
    async with world.client() as c:
        assert (await c.get("/api/v1/health")).status_code == 200
        assert (await c.get("/api/v1/health/ready")).status_code == 200


def _claims(**over) -> dict:
    now = int(time.time())
    base = {
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "roles": ["member"],
        "iss": AUTH.issuer,
        "aud": AUTH.audience,
        "iat": now,
        "exp": now + 900,
        "typ": "access",
        "jti": uuid.uuid4().hex,
    }
    base.update(over)
    return base


async def test_forged_and_malformed_tokens_are_rejected(world, seed):
    """Every failure mode must produce the SAME 401 — no oracle for the attacker."""
    a1 = world.actors["a1"]
    good = issue_access_token(
        AUTH, user_id=a1.user_id, tenant_id=a1.tenant_id, roles=a1.roles
    )
    now = int(time.time())

    forged = {
        "wrong-secret": jwt.encode(_claims(), "not-the-secret", algorithm="HS256"),
        "alg-none": jwt.encode(_claims(), key="", algorithm="none"),
        "wrong-typ": jwt.encode(_claims(typ="refresh"), AUTH.secret, algorithm="HS256"),
        "missing-tid": jwt.encode(
            {k: v for k, v in _claims().items() if k != "tid"},
            AUTH.secret,
            algorithm="HS256",
        ),
        "expired": jwt.encode(
            _claims(iat=now - 4000, exp=now - 3000), AUTH.secret, algorithm="HS256"
        ),
        "wrong-issuer": jwt.encode(
            _claims(iss="evil"), AUTH.secret, algorithm="HS256"
        ),
        "garbage": "not.a.jwt",
    }

    async with world.client() as c:
        # control: a valid token works
        ok = await c.get("/api/v1/conversations", headers=bearer(good))
        assert ok.status_code == 200

        for name, token in forged.items():
            resp = await c.get("/api/v1/conversations", headers=bearer(token))
            assert resp.status_code == 401, f"{name} -> {resp.status_code}"
            assert resp.json()["detail"] == "Not authenticated"
            assert resp.headers.get("WWW-Authenticate") == "Bearer"


async def test_google_exchange_issues_backend_token(world, seed):
    from app.auth import GoogleIdentity

    a1 = world.actors["a1"]
    FAKE_IDENTITIES["tok-a1"] = GoogleIdentity(
        sub="sub-a1", email="a1@example.com", email_verified=True
    )

    async with world.client() as c:
        r = await c.post("/api/v1/auth/google", json={"id_token": "tok-a1"})
    assert r.status_code == 200, r.text
    claims = jwt.decode(
        r.json()["access_token"], AUTH.secret, algorithms=["HS256"],
        audience=AUTH.audience, issuer=AUTH.issuer,
    )
    assert claims["sub"] == str(a1.user_id)
    assert claims["tid"] == str(a1.tenant_id)
    assert claims["roles"] == ["member"]
    assert claims["typ"] == "access"


async def test_google_exchange_invite_only(world, seed):
    from app.auth import GoogleIdentity

    FAKE_IDENTITIES["tok-new"] = GoogleIdentity(
        sub="sub-stranger", email="stranger@example.com", email_verified=True
    )
    FAKE_IDENTITIES["tok-unverified"] = GoogleIdentity(
        sub="sub-a1", email="a1@example.com", email_verified=False
    )

    async with world.client() as c:
        # seeded user, unverified email -> 401
        r = await c.post("/api/v1/auth/google", json={"id_token": "tok-unverified"})
        assert r.status_code == 401

        # known google_sub but NO membership -> 403 (invite-only, no auto-provision)
        r = await c.post("/api/v1/auth/google", json={"id_token": "tok-new"})
        assert r.status_code == 403

        # unknown/forged id_token -> 401 (same message as other auth failures)
        r = await c.post("/api/v1/auth/google", json={"id_token": "tok-bogus"})
        assert r.status_code == 401
        assert r.json()["detail"] == "Not authenticated"

        # extra fields are rejected (extra="forbid")
        r = await c.post(
            "/api/v1/auth/google",
            json={"id_token": "tok-a1", "tenant_id": "x", "roles": ["admin"]},
        )
        assert r.status_code == 422


async def test_disabled_user_loses_access(world, seed):
    from sqlalchemy import text

    a1 = world.actors["a1"]
    async with world.engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET disabled_at = now() WHERE id = :uid"),
            {"uid": a1.user_id},
        )

    # revocation: existing token stops working (checked on every request in tests)
    async with world.client(a1) as c:
        r = await c.get("/api/v1/conversations")
        assert r.status_code == 401
        assert r.json()["detail"] == "Not authenticated"

    # and Google exchange refuses to mint a new one
    from app.auth import GoogleIdentity

    FAKE_IDENTITIES["tok-a1"] = GoogleIdentity(
        sub="sub-a1", email="a1@example.com", email_verified=True
    )
    async with world.client() as c:
        r = await c.post("/api/v1/auth/google", json={"id_token": "tok-a1"})
        assert r.status_code == 403


async def test_removal_from_tenant_revokes_access(world, seed):
    """Membership gone -> token no longer resolves to an active member."""
    from sqlalchemy import text

    # a_admin owns no documents, so the documents -> memberships FK allows
    # removing the membership (offboarding requires reassigning documents).
    admin = world.actors["a_admin"]
    async with world.engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM memberships WHERE user_id = :uid"),
            {"uid": admin.user_id},
        )

    async with world.client(admin) as c:
        r = await c.get("/api/v1/conversations")
        assert r.status_code == 401


async def test_dev_auth_disabled_allows_anonymous(world):
    """AUTH_DISABLED=true: no token, but a dev Principal is still bound."""
    app = create_app(
        auth=AUTH,
        google_verifier=world.app.state.google_verifier,
        active_checker=cached_active_checker(_db_is_active, ttl_seconds=0),
        engine=world.engine,
        auth_disabled=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/conversations")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}

"""Integration tests: REAL PostgreSQL, no mocks (ported from rag_auth).

Set TEST_DATABASE_URL to a DISPOSABLE database — the session fixture
DROPS SCHEMA `public` and re-runs every Alembic migration against it.

    docker compose -f tests/integration/docker-compose.test.yml up -d --wait
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ragtest \
        pytest tests/integration -q

Topology: 2 tenants (A, B), 5 actors (a1, a2, a_admin, b1, b_admin) plus
invite-only login checks. Every test re-seeds from a clean slate.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth import (
    AuthSettings,
    GoogleIdentity,
    cached_active_checker,
    issue_access_token,
)
from app.config import settings
from app.main import _db_is_active, create_app

BACKEND = Path(__file__).resolve().parents[2]
DSN = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/ragtest",
)
PG_DSN = DSN.replace("+asyncpg", "")

AUTH = AuthSettings(secret="test-secret-" + "x" * 40)

PUBLIC_PATHS = {
    "/api/v1/health",
    "/api/v1/health/ready",
    "/api/v1/auth/google",
}


# ---- database reset (session-scoped, runs before anything else) -------------------------
@pytest.fixture(scope="session", autouse=True)
def _database():
    import asyncpg

    async def drop_and_recreate():
        conn = await asyncpg.connect(PG_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
        finally:
            await conn.close()

    asyncio.run(drop_and_recreate())

    # migrations/env.py reads settings.database_url at execution time
    settings.database_url = DSN
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(DSN, poolclass=NullPool)
    yield eng
    await eng.dispose()


# ---- fake Google verifier (signature checked by google-auth in production) ---------------
FAKE_IDENTITIES: dict[str, GoogleIdentity] = {}


async def _fake_google_verifier(id_token: str) -> GoogleIdentity:
    if id_token not in FAKE_IDENTITIES:
        raise ValueError("invalid id_token")
    return FAKE_IDENTITIES[id_token]


@pytest.fixture(autouse=True)
def fake_identities():
    FAKE_IDENTITIES.clear()
    yield
    FAKE_IDENTITIES.clear()


# ---- actors ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Actor:
    name: str
    user_id: UUID
    tenant_id: UUID
    roles: frozenset[str]

    @property
    def token(self) -> str:
        return issue_access_token(
            AUTH, user_id=self.user_id, tenant_id=self.tenant_id, roles=self.roles
        )


class World:
    def __init__(self, app, engine):
        self.app = app
        self.engine = engine
        self.actors: dict[str, Actor] = {}

    def client(self, actor: Actor | None = None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {actor.token}"} if actor else {}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            headers=headers,
        )

    def session_factory(self) -> async_sessionmaker:
        """Usage: async with world.session_factory()() as session: ..."""
        return async_sessionmaker(self.engine, expire_on_commit=False)

    async def sql(self, query: str, **params):
        async with self.engine.begin() as conn:
            result = await conn.execute(text(query), params)
            return result.fetchall()

    async def count(self, table: str) -> int:
        rows = await self.sql(f"SELECT count(*) FROM {table}")
        return rows[0][0]


@pytest_asyncio.fixture
async def world(engine):
    app = create_app(
        auth=AUTH,
        google_verifier=_fake_google_verifier,
        # ttl 0: revocation is checked on every request (a disabled or
        # removed member must lose access immediately in tests)
        active_checker=cached_active_checker(_db_is_active, ttl_seconds=0),
        engine=engine,
    )
    w = World(app, engine)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE documents, conversations, messages, agent_runs, "
                "memberships, users, tenants RESTART IDENTITY CASCADE"
            )
        )
    _seed_actors(w)
    yield w


def _seed_actors(w: World) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    roles = {
        "a1": frozenset({"member"}),
        "a2": frozenset({"member"}),
        "a_admin": frozenset({"member", "admin"}),
        "b1": frozenset({"member"}),
        "b_admin": frozenset({"member", "admin"}),
    }
    tenants = {"a": tenant_a, "b": tenant_b}
    for name, roleset in roles.items():
        w.actors[name] = Actor(
            name=name,
            user_id=uuid.uuid4(),
            tenant_id=tenants[name[0]],
            roles=roleset,
        )


# ---- seed: identity + documents + conversation --------------------------------------------
@dataclass
class Seed:
    tenant_a: str
    tenant_b: str
    conv_a1: UUID
    doc_a1_personal: UUID
    doc_a1_org: UUID
    doc_a2_org: UUID
    doc_b1_org: UUID
    doc_b1_personal: UUID


async def _make_doc(session, *, doc_id, tenant_id, owner_id, scope, filename, chunk_set):
    from app.infrastructure.database.models import DocumentRecord

    session.add(
        DocumentRecord(
            id=doc_id,
            tenant_id=tenant_id,
            knowledge_base_id="kb-1",
            user_id=owner_id,
            scope=scope,
            filename=filename,
            storage_path=f"tenants/{tenant_id}/documents/{doc_id}/{filename}",
            file_hash="0" * 64,
            file_type=filename.rsplit(".", 1)[-1],
            status="ready",
            active_chunk_set_id=chunk_set,
        )
    )


@pytest_asyncio.fixture
async def seed(world):
    from app.infrastructure.database.models import (
        ConversationRecord,
        MembershipRecord,
        MessageRecord,
        TenantRecord,
        UserRecord,
    )

    a1, a2, a_admin, b1, b_admin = (
        world.actors[n] for n in ("a1", "a2", "a_admin", "b1", "b_admin")
    )
    tenant_a, tenant_b = str(a1.tenant_id), str(b1.tenant_id)

    async with world.session_factory()() as session:
        session.add_all(
            [
                TenantRecord(id=tenant_a, name="Tenant A"),
                TenantRecord(id=tenant_b, name="Tenant B"),
            ]
        )
        for actor in (a1, a2, a_admin, b1, b_admin):
            session.add(
                UserRecord(
                    id=actor.user_id,
                    google_sub=f"sub-{actor.name}",
                    email=f"{actor.name}@example.com",
                )
            )
            session.add(
                MembershipRecord(
                    user_id=actor.user_id,
                    tenant_id=str(actor.tenant_id),
                    roles=sorted(actor.roles),
                )
            )

        docs = {
            "doc_a1_personal": uuid.uuid4(),
            "doc_a1_org": uuid.uuid4(),
            "doc_a2_org": uuid.uuid4(),
            "doc_b1_org": uuid.uuid4(),
            "doc_b1_personal": uuid.uuid4(),
        }
        await _make_doc(
            session, doc_id=docs["doc_a1_personal"], tenant_id=tenant_a,
            owner_id=a1.user_id, scope="personal",
            filename="personal-a1.pdf", chunk_set="cs-a1-personal",
        )
        await _make_doc(
            session, doc_id=docs["doc_a1_org"], tenant_id=tenant_a,
            owner_id=a1.user_id, scope="org",
            filename="org-a1.pdf", chunk_set="cs-a1-org",
        )
        await _make_doc(
            session, doc_id=docs["doc_a2_org"], tenant_id=tenant_a,
            owner_id=a2.user_id, scope="org",
            filename="org-a2.pdf", chunk_set="cs-a2-org",
        )
        await _make_doc(
            session, doc_id=docs["doc_b1_org"], tenant_id=tenant_b,
            owner_id=b1.user_id, scope="org",
            filename="org-b1.pdf", chunk_set="cs-b1-org",
        )
        await _make_doc(
            session, doc_id=docs["doc_b1_personal"], tenant_id=tenant_b,
            owner_id=b1.user_id, scope="personal",
            filename="personal-b1.pdf", chunk_set="cs-b1-personal",
        )

        conv_a1 = uuid.uuid4()
        session.add(
            ConversationRecord(
                id=conv_a1, tenant_id=tenant_a, user_id=a1.user_id,
                title="Conversation A1",
            )
        )
        session.add(
            MessageRecord(
                conversation_id=conv_a1, tenant_id=tenant_a, user_id=a1.user_id,
                role="user", content="SECRET-answer-42",
            )
        )
        await session.commit()

    return Seed(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        conv_a1=conv_a1,
        **docs,
    )


# ---- helpers -------------------------------------------------------------------------------
def parse_sse(body: str) -> dict[str, dict]:
    """Parse an SSE body into {event_name: data} (last frame wins)."""
    events: dict[str, dict] = {}
    event, data = None, None
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = json.loads(line[5:].strip())
        elif not line and event is not None:
            events[event] = data
            event, data = None, None
    if event is not None:
        events[event] = data
    return events


async def chat(world, who: str, conversation_id, **extra) -> dict[str, dict]:
    async with world.client(world.actors[who]) as c:
        r = await c.post(
            "/api/v1/chat",
            json={"conversation_id": str(conversation_id), "query": "q", **extra},
        )
    assert r.status_code == 200, r.text
    return parse_sse(r.text)

"""Call repositories DIRECTLY (bypassing the API gate) to prove each layer
protects itself — for the day a future route/worker/tool forgets the layer
above. Also asserts the DB-level constraints: even with buggy code, Postgres
refuses rows whose owner/conversation belongs to a different tenant.

Ported from the rag_auth package; adapted to ORM repositories and this
project's schema (String(64) tenant ids, soft-delete status='deleting').
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import Principal
from app.infrastructure.database.models import DocumentRecord, MessageRecord
from app.infrastructure.database.repositories import (
    AgentRunRepository,
    ConversationRepository,
    DocumentRepository,
)


def principal(actor) -> Principal:
    return Principal(actor.user_id, actor.tenant_id, actor.roles)


async def test_conversation_repo_refuses_non_owner_even_when_called_directly(world, seed):
    cid = seed.conv_a1
    msgs_before = await world.count("messages")
    runs_before = await world.count("agent_runs")

    for who in ("a2", "a_admin", "b1"):
        async with world.session_factory()() as session:
            repo = ConversationRepository(session, principal(world.actors[who]))
            assert await repo.get(cid) is None
            assert await repo.messages(cid) is None
            assert await repo.add_message(cid, "user", "INJECTED") is None
            assert await repo.delete(cid) is False

            run_repo = AgentRunRepository(session, principal(world.actors[who]))
            assert await run_repo.create(cid, query="INJECTED", snapshot={}) is None

    assert await world.count("messages") == msgs_before
    assert await world.count("agent_runs") == runs_before


async def test_owner_path_works_end_to_end(world, seed):
    a1 = world.actors["a1"]
    async with world.session_factory()() as session:
        convs = ConversationRepository(session, principal(a1))
        msg = await convs.add_message(seed.conv_a1, "user", "hello from a1")
        assert msg is not None
        assert str(msg.tenant_id) == seed.tenant_a
        assert msg.user_id == a1.user_id

        runs = AgentRunRepository(session, principal(a1))
        run = await runs.create(seed.conv_a1, query="q", snapshot={seed.doc_a1_org: "cs-1"})
        assert run is not None
        assert run.snapshot == {str(seed.doc_a1_org): "cs-1"}

        # owner can update their own run
        await runs.update(run.id, status="completed", final_answer="done")
        fetched = await runs.get(run.id)
        assert fetched.status == "completed"
        assert fetched.final_answer == "done"


async def test_agent_run_update_refuses_foreign_run(world, seed):
    a1, a2 = world.actors["a1"], world.actors["a2"]
    async with world.session_factory()() as session:
        run = await AgentRunRepository(session, principal(a1)).create(
            seed.conv_a1, query="q", snapshot={}
        )
    assert run is not None

    async with world.session_factory()() as session:
        other = AgentRunRepository(session, principal(a2))
        assert await other.get(run.id) is None
        await other.update(run.id, status="completed", final_answer="INJECTED")
        # no crash, no write: the UPDATE went through an ownership-gated SELECT

    async with world.session_factory()() as session:
        fetched = await AgentRunRepository(session, principal(a1)).get(run.id)
        assert fetched.status == "running"       # untouched
        assert fetched.final_answer is None


async def test_can_manage_matrix(world, seed):
    a1, a2, a_admin = (world.actors[n] for n in ("a1", "a2", "a_admin"))
    async with world.session_factory()() as session:
        personal = await DocumentRepository(session, principal(a1)).get(seed.doc_a1_personal)
        org = await DocumentRepository(session, principal(a1)).get(seed.doc_a1_org)
        assert personal is not None and org is not None

        # owner can always manage their own documents
        assert DocumentRepository(session, principal(a1)).can_manage(personal) is True
        assert DocumentRepository(session, principal(a1)).can_manage(org) is True

        # personal: admin does NOT outrank the owner
        assert DocumentRepository(session, principal(a_admin)).can_manage(personal) is False

        # org: owner or admin
        assert DocumentRepository(session, principal(a_admin)).can_manage(org) is True

        # other member: neither
        assert DocumentRepository(session, principal(a2)).can_manage(personal) is False
        assert DocumentRepository(session, principal(a2)).can_manage(org) is False


async def test_visibility_and_snapshot_in_repo(world, seed):
    a1, b1, a_admin = world.actors["a1"], world.actors["b1"], world.actors["a_admin"]

    async with world.session_factory()() as session:
        repo = DocumentRepository(session, principal(a1))
        visible = {d.id for d in await repo.list()}
        assert visible == {seed.doc_a1_personal, seed.doc_a1_org, seed.doc_a2_org}

        # a1's full snapshot = visible docs that are active + not quarantined
        snapshot = await repo.active_chunk_sets()
        assert set(snapshot) == visible

        # narrowing: a foreign document id yields nothing (never widens)
        assert await repo.active_chunk_sets([seed.doc_b1_org]) == {}
        assert set(await repo.active_chunk_sets([seed.doc_a1_org])) == {seed.doc_a1_org}

        # quarantined documents drop out of the snapshot
        admin_repo = DocumentRepository(session, principal(a_admin))
        quarantined = await admin_repo.quarantine(seed.doc_a1_org)
        assert quarantined is not None and quarantined.quarantined_at is not None
        snapshot = await repo.active_chunk_sets()
        assert seed.doc_a1_org not in snapshot
        # ...but personal + other org docs remain
        assert snapshot == {seed.doc_a1_personal: "cs-a1-personal",
                            seed.doc_a2_org: "cs-a2-org"}

        # tenant B isolation
        b_repo = DocumentRepository(session, principal(b1))
        b_visible = {d.id for d in await b_repo.list()}
        assert b_visible == {seed.doc_b1_org, seed.doc_b1_personal}


async def test_db_rejects_cross_tenant_and_invalid_rows(world, seed):
    """The composite FKs / CHECK constraints are the last line of defence."""
    a1, tenant_a, tenant_b = world.actors["a1"], seed.tenant_a, seed.tenant_b

    # 1) message pointing at another tenant's conversation
    async with world.session_factory()() as session:
        with pytest.raises(IntegrityError):
            session.add(
                MessageRecord(
                    conversation_id=seed.conv_a1,
                    tenant_id=tenant_b,
                    user_id=a1.user_id,
                    role="user",
                    content="cross-tenant message",
                )
            )
            await session.commit()
        await session.rollback()

    # 2) message whose user differs from the conversation's owner
    async with world.session_factory()() as session:
        with pytest.raises(IntegrityError):
            session.add(
                MessageRecord(
                    conversation_id=seed.conv_a1,
                    tenant_id=tenant_a,
                    user_id=world.actors["a2"].user_id,   # same tenant, other user
                    role="user",
                    content="wrong-owner message",
                )
            )
            await session.commit()
        await session.rollback()

    # 3) document whose owner is not a member of that tenant
    async with world.session_factory()() as session:
        with pytest.raises(IntegrityError):
            session.add(
                DocumentRecord(
                    id=uuid.uuid4(),
                    tenant_id=tenant_b,          # a1 belongs to tenant A only
                    knowledge_base_id="kb-1",
                    user_id=a1.user_id,
                    scope="org",
                    filename="x.pdf",
                    storage_path="p",
                    file_hash="0" * 64,
                    file_type="pdf",
                )
            )
            await session.commit()
        await session.rollback()

    # 4) scope has NO DEFAULT and only accepts org|personal
    async with world.session_factory()() as session:
        with pytest.raises(IntegrityError):     # NOT NULL (no default)
            await session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, knowledge_base_id, user_id, "
                    "filename, storage_path, file_hash, file_type) "
                    "VALUES (:id, :tid, 'kb-1', :uid, 'x.pdf', 'p', :hash, 'pdf')"
                ),
                {"id": uuid.uuid4(), "tid": tenant_a, "uid": a1.user_id, "hash": "0" * 64},
            )
        await session.rollback()

        with pytest.raises(IntegrityError):     # CHECK constraint
            await session.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, knowledge_base_id, user_id, "
                    "scope, filename, storage_path, file_hash, file_type) "
                    "VALUES (:id, :tid, 'kb-1', :uid, 'public', 'x.pdf', 'p', :hash, 'pdf')"
                ),
                {"id": uuid.uuid4(), "tid": tenant_a, "uid": a1.user_id, "hash": "0" * 64},
            )
        await session.rollback()

    # 5) agent run pointing at another tenant's conversation
    async with world.session_factory()() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO agent_runs (id, tenant_id, conversation_id, user_id, "
                    "query, snapshot) VALUES (:id, :tid, :cid, :uid, 'q', '{}')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tid": tenant_b,
                    "cid": seed.conv_a1,
                    "uid": a1.user_id,
                },
            )
        await session.rollback()

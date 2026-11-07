"""IDOR: tenant isolation + per-user privacy (conversations) + document visibility.

Every "not yours" request must look exactly like "does not exist" (404 with
the same body), and rejected requests must not write anything.
"""
import uuid

# actors touching a1's conversation: same tenant other user, same tenant
# admin, other tenant user, other tenant admin
OTHERS_FOR_CONVERSATION = ["a2", "a_admin", "b1", "b_admin"]


async def test_conversation_endpoints_hidden_from_non_owner(world, seed):
    cid = seed.conv_a1
    for who in OTHERS_FOR_CONVERSATION:
        runs_before = await world.count("agent_runs")
        msgs_before = await world.count("messages")

        async with world.client(world.actors[who]) as c:
            for method, path, body in [
                ("GET", f"/api/v1/conversations/{cid}", None),
                ("GET", f"/api/v1/conversations/{cid}/messages", None),
                ("POST", "/api/v1/chat", {"conversation_id": str(cid), "query": "hi"}),
            ]:
                r = await c.request(method, path, json=body)
                assert r.status_code == 404, f"{who} {method} {path} -> {r.status_code}"
                assert "SECRET" not in r.text
                assert r.json()["detail"] == "Not found"

        # rejected requests must not create runs or messages
        assert await world.count("agent_runs") == runs_before
        assert await world.count("messages") == msgs_before


async def test_conversation_listing_is_private_per_user(world, seed):
    async with world.client(world.actors["a2"]) as c:
        created = await c.post("/api/v1/conversations", json={"title": "A2 private"})
        assert created.status_code == 201
        other_id = created.json()["id"]

    # a2 sees only its own conversation; a1's title must not leak
    async with world.client(world.actors["a2"]) as c:
        r = await c.get("/api/v1/conversations")
    assert [item["id"] for item in r.json()["items"]] == [other_id]

    async with world.client(world.actors["a1"]) as c:
        r = await c.get("/api/v1/conversations")
    ids = [item["id"] for item in r.json()["items"]]
    assert str(seed.conv_a1) in ids
    assert other_id not in ids
    assert "A2 private" not in r.text

    # and a1 cannot fetch a2's conversation directly
    async with world.client(world.actors["a1"]) as c:
        r = await c.get(f"/api/v1/conversations/{other_id}")
        assert r.status_code == 404


async def test_document_endpoints_hidden_from_outsiders(world, seed):
    cases = [
        # (actor, document, expected) — invisible resources are 404, never 403
        ("a2", seed.doc_a1_personal, 404),
        ("a_admin", seed.doc_a1_personal, 404),   # admin does NOT see personal docs of others
        ("b1", seed.doc_a1_org, 404),             # other tenant
        ("b_admin", seed.doc_a1_org, 404),
    ]
    for who, doc_id, expected in cases:
        async with world.client(world.actors[who]) as c:
            for method, path in [
                ("GET", f"/api/v1/documents/{doc_id}"),
                ("GET", f"/api/v1/documents/{doc_id}/chunks"),
                ("DELETE", f"/api/v1/documents/{doc_id}"),
            ]:
                r = await c.request(method, path)
                assert r.status_code == expected, f"{who} {method} {path} -> {r.status_code}"
                assert "personal-a1" not in r.text


async def test_delete_gating_matrix(world, seed):
    # visible org doc, but neither owner nor admin -> 403 (not 404: it IS visible)
    async with world.client(world.actors["a2"]) as c:
        r = await c.delete(f"/api/v1/documents/{seed.doc_a1_org}")
        assert r.status_code == 403

    # other tenant -> 404 (cannot even see it)
    async with world.client(world.actors["b1"]) as c:
        r = await c.delete(f"/api/v1/documents/{seed.doc_a1_org}")
        assert r.status_code == 404

    # admin CAN manage an org document of someone else -> soft delete
    async with world.client(world.actors["a_admin"]) as c:
        r = await c.delete(f"/api/v1/documents/{seed.doc_a2_org}")
        assert r.status_code == 204
    async with world.client(world.actors["a2"]) as c:
        r = await c.get(f"/api/v1/documents/{seed.doc_a2_org}")
        assert r.status_code == 200          # owner still sees it...
        assert r.json()["status"] == "deleting"   # ...as deleted (soft delete)

    # admin does NOT outrank personal: not even visible -> 404
    async with world.client(world.actors["a_admin"]) as c:
        r = await c.delete(f"/api/v1/documents/{seed.doc_a1_personal}")
        assert r.status_code == 404

    # owner deletes own org document -> 204, soft-deleted
    async with world.client(world.actors["a1"]) as c:
        r = await c.delete(f"/api/v1/documents/{seed.doc_a1_org}")
        assert r.status_code == 204
        r = await c.get(f"/api/v1/documents/{seed.doc_a1_org}")
        assert r.json()["status"] == "deleting"

    # deleted documents disappear from listings
    async with world.client(world.actors["a1"]) as c:
        r = await c.get("/api/v1/documents")
    listed = {item["id"] for item in r.json()["items"]}
    assert str(seed.doc_a1_org) not in listed
    assert str(seed.doc_a1_personal) in listed


async def test_client_cannot_inject_tenant_or_owner(world, seed):
    """extra="forbid": client-supplied tenant_id/owner_id are rejected outright."""
    async with world.client(world.actors["a1"]) as c:
        r = await c.post(
            "/api/v1/conversations",
            json={"title": "x", "tenant_id": seed.tenant_b, "user_id": str(uuid.uuid4())},
        )
        assert r.status_code == 422

        r = await c.post(
            "/api/v1/chat",
            json={
                "conversation_id": str(seed.conv_a1),
                "query": "q",
                "tenant_id": seed.tenant_b,
            },
        )
        assert r.status_code == 422


async def test_chat_with_foreign_document_ids_stays_empty(world, seed):
    """document_ids can only NARROW the snapshot — a foreign id yields nothing."""
    from tests.integration.conftest import chat

    events = await chat(
        world, "a1", seed.conv_a1, document_ids=[str(seed.doc_b1_org)]
    )
    assert events["run_started"]["snapshot_document_ids"] == []


async def test_snapshot_matches_exactly_visible_documents(world, seed):
    from tests.integration.conftest import chat

    visible_a1 = {
        str(seed.doc_a1_personal),
        str(seed.doc_a1_org),
        str(seed.doc_a2_org),
    }
    events = await chat(world, "a1", seed.conv_a1)
    assert set(events["run_started"]["snapshot_document_ids"]) == visible_a1

    # b1's own conversation: only tenant B documents
    async with world.client(world.actors["b1"]) as c:
        conv = await c.post("/api/v1/conversations", json={"title": "b1 conv"})
        conv_id = conv.json()["id"]
    events = await chat(
        world, "b1", conv_id,
        document_ids=[str(seed.doc_b1_org), str(seed.doc_a1_org)],  # a1's id must not widen
    )
    assert events["run_started"]["snapshot_document_ids"] == [str(seed.doc_b1_org)]


async def test_quarantine_requires_admin_and_hides_from_snapshot(world, seed):
    # member (even the owner) cannot quarantine
    async with world.client(world.actors["a1"]) as c:
        r = await c.post(f"/api/v1/documents/{seed.doc_a1_org}/quarantine")
        assert r.status_code == 403

    # admin quarantines an org document
    async with world.client(world.actors["a_admin"]) as c:
        r = await c.post(f"/api/v1/documents/{seed.doc_a1_org}/quarantine")
        assert r.status_code == 200
        assert r.json()["quarantined"] is True

    # ...and it drops out of the retrieval snapshot for everyone
    from tests.integration.conftest import chat

    events = await chat(world, "a1", seed.conv_a1)
    assert str(seed.doc_a1_org) not in events["run_started"]["snapshot_document_ids"]
    assert str(seed.doc_a1_personal) in events["run_started"]["snapshot_document_ids"]

    # personal documents cannot be quarantined at all (404: not an org doc)
    async with world.client(world.actors["a_admin"]) as c:
        r = await c.post(f"/api/v1/documents/{seed.doc_a1_personal}/quarantine")
        assert r.status_code == 404

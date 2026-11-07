"""Visibility: document scope rules, listing privacy, scope validation.

Filenames are used as the canary: a document that must not be visible must
not leak its filename through any response body.
"""
from tests.integration.conftest import chat


async def test_scope_is_required_and_validated(world, seed):
    """scope has no DEFAULT: missing / invalid / null -> 422 at the boundary."""
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    files = {"file": ("x.pdf", pdf, "application/pdf")}

    async with world.client(world.actors["a1"]) as c:
        # missing scope
        r = await c.post("/api/v1/documents", files=files)
        assert r.status_code == 422

        # invalid value
        r = await c.post("/api/v1/documents", files=files, data={"scope": "public"})
        assert r.status_code == 422

        # explicit null
        r = await c.post("/api/v1/documents", files=files, data={"scope": ""})
        assert r.status_code == 422

        # valid scope passes validation and stops at the (pending) storage wiring
        r = await c.post("/api/v1/documents", files=files, data={"scope": "org"})
        assert r.status_code == 501, r.text


async def test_listing_visibility_per_actor(world, seed):
    expected = {
        "a1": {seed.doc_a1_personal, seed.doc_a1_org, seed.doc_a2_org},
        "a2": {seed.doc_a1_org, seed.doc_a2_org},
        "a_admin": {seed.doc_a1_org, seed.doc_a2_org},
        "b1": {seed.doc_b1_org, seed.doc_b1_personal},
        # admin does NOT outrank personal documents owned by someone else
        "b_admin": {seed.doc_b1_org},
    }
    for who, doc_ids in expected.items():
        async with world.client(world.actors[who]) as c:
            r = await c.get("/api/v1/documents")
        assert r.status_code == 200
        items = r.json()["items"]
        assert {item["id"] for item in items} == {str(d) for d in doc_ids}, who

    # cross-tenant filename must never appear in another tenant's listing
    async with world.client(world.actors["b1"]) as c:
        r = await c.get("/api/v1/documents")
    assert "personal-a1.pdf" not in r.text
    assert "org-a1.pdf" not in r.text

    # a2 must not see a1's personal document (or its filename)
    async with world.client(world.actors["a2"]) as c:
        r = await c.get("/api/v1/documents")
    assert "personal-a1.pdf" not in r.text
    assert "org-a2.pdf" in r.text

    # owned_by_me flag reflects the caller, not a global truth
    async with world.client(world.actors["a1"]) as c:
        r = await c.get("/api/v1/documents")
    flags = {item["filename"]: item["owned_by_me"] for item in r.json()["items"]}
    assert flags["personal-a1.pdf"] is True
    assert flags["org-a2.pdf"] is False


async def test_get_document_and_chunks_gated(world, seed):
    # owner: full access, including the chunk sub-resource
    async with world.client(world.actors["a1"]) as c:
        r = await c.get(f"/api/v1/documents/{seed.doc_a1_personal}")
        assert r.status_code == 200
        assert r.json()["scope"] == "personal"
        r = await c.get(f"/api/v1/documents/{seed.doc_a1_personal}/chunks")
        assert r.status_code == 200

    # same tenant, different user: org doc visible, personal doc invisible
    async with world.client(world.actors["a2"]) as c:
        r = await c.get(f"/api/v1/documents/{seed.doc_a1_org}")
        assert r.status_code == 200
        r = await c.get(f"/api/v1/documents/{seed.doc_a1_org}/chunks")
        assert r.status_code == 200
        r = await c.get(f"/api/v1/documents/{seed.doc_a1_personal}")
        assert r.status_code == 404
        r = await c.get(f"/api/v1/documents/{seed.doc_a1_personal}/chunks")
        assert r.status_code == 404


async def test_messages_history_private(world, seed):
    # owner sees the seeded message
    async with world.client(world.actors["a1"]) as c:
        r = await c.get(f"/api/v1/conversations/{seed.conv_a1}/messages")
    assert r.status_code == 200
    contents = [m["content"] for m in r.json()]
    assert "SECRET-answer-42" in contents

    # nobody else gets the history (404, no leak)
    for who in ("a2", "a_admin", "b1", "b_admin"):
        async with world.client(world.actors[who]) as c:
            r = await c.get(f"/api/v1/conversations/{seed.conv_a1}/messages")
        assert r.status_code == 404
        assert "SECRET" not in r.text


async def test_chat_persists_user_message_and_reports_run(world, seed):
    events = await chat(world, "a1", seed.conv_a1)

    # contract: run_started -> status/token -> completed
    assert "run_started" in events
    assert "completed" in events
    assert "error" not in events
    assert events["run_started"]["snapshot_document_ids"]

    async with world.client(world.actors["a1"]) as c:
        r = await c.get(f"/api/v1/conversations/{seed.conv_a1}/messages")
    contents = [m["content"] for m in r.json()]
    assert "q" in contents          # the user's message was persisted
    assert "Fake answer." in contents  # the assistant message was persisted
    assert "SECRET-answer-42" in contents

    # the run row belongs to the caller and finished successfully
    rows = await world.sql(
        "SELECT tenant_id, user_id, status, model_name "
        "FROM agent_runs ORDER BY created_at DESC"
    )
    assert rows, "chat must create an agent_runs row"
    assert rows[0][0] == seed.tenant_a
    assert str(rows[0][1]) == str(world.actors["a1"].user_id)
    assert rows[0][2] == "completed"
    assert rows[0][3] == events["run_started"]["model"]


async def test_deleted_document_leaves_snapshot(world, seed):
    async with world.client(world.actors["a1"]) as c:
        r = await c.delete(f"/api/v1/documents/{seed.doc_a1_org}")
        assert r.status_code == 204

    events = await chat(world, "a1", seed.conv_a1)
    assert str(seed.doc_a1_org) not in events["run_started"]["snapshot_document_ids"]
    assert str(seed.doc_a1_personal) in events["run_started"]["snapshot_document_ids"]


async def test_model_catalogue_and_chat_model_validation(world, seed):
    async with world.client(world.actors["a1"]) as c:
        r = await c.get("/api/v1/models")
        assert r.status_code == 200
        body = r.json()
        ids = {item["id"] for item in body["items"]}
        assert body["default"] in ids

        # a model outside the server allowlist is rejected
        bad = await c.post(
            "/api/v1/chat",
            json={
                "conversation_id": str(seed.conv_a1),
                "query": "q",
                "model": "not-an-allowed-model",
            },
        )
        assert bad.status_code == 400

    # a valid selection is echoed and recorded on the run
    events = await chat(world, "a1", seed.conv_a1, model=body["default"])
    assert events["run_started"]["model"] == body["default"]

    rows = await world.sql(
        "SELECT model_name, model_provider FROM agent_runs ORDER BY created_at DESC"
    )
    assert rows[0][0] == body["default"]
    assert rows[0][1] == next(
        item["provider"] for item in body["items"] if item["id"] == body["default"]
    )

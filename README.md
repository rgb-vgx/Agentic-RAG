# Agentic RAG — Document Q&A Chatbot

Adaptive Document Intelligence & Question Answering System.
Implements `Proposal Design.md` (Technical Design v1.1).

```
Agentic-RAG/
├── Proposal Design.md              # Technical design v1.1 (EN)
├── Proposal Design (Tiếng Việt).md # Bản tiếng Việt, line-aligned
├── docker-compose.yml              # Postgres + Redis + Qdrant + MinIO + API + worker + frontend
├── backend/                        # Python 3.12 + FastAPI
│   ├── app/
│   │   ├── api/v1/                 # REST + SSE endpoints (auth, documents, conversations, chat)
│   │   ├── auth.py                 # Principal, access JWT, Google verifier, revocation check
│   │   ├── domain/                 # Entities & workflow state (no framework deps)
│   │   ├── ingestion/              # Parse → IR → quality → sections → chunk → embed → index
│   │   │   └── chunking/           # 9 strategies + evaluation + constraint-first selection
│   │   ├── agentic_rag/            # LangGraph workflow, prompts, retrieval, evaluation, validation
│   │   ├── infrastructure/         # LLM, embeddings, Qdrant, SQLAlchemy, S3, Langfuse
│   │   ├── services/               # Chat & document use-cases
│   │   └── workers/                # RQ ingestion jobs
│   ├── migrations/                 # Alembic (0001–0003)
│   ├── conf/conf.json              # Non-secret runtime config (env overrides)
│   └── tests/                      # 49 unit tests (no infra) + integration/ (29, real Postgres)
└── frontend/                       # Next.js 15 (App Router) + TypeScript
    ├── conf/conf.json              # Frontend config, bundled at build time
    └── src/
        ├── app/                    # /chat, /documents pages (self-contained)
        ├── lib/                    # api.ts (REST), stream.ts (SSE via fetch), types.ts
        └── store/                  # zustand chat store
```

## Quick start

```bash
docker compose up --build
# API docs:  http://localhost:8000/docs
# Frontend:  http://localhost:3000
# Postgres:  localhost:5436 (agentic/agentic)  |  Qdrant: localhost:6333/6334
```

> `docker-compose.yml` uses `backend/.env.example` as its env file. Fill in
> `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID` and a
> non-default `JWT_SECRET` there (or change `env_file`) before expecting
> ingestion or login to work.

Backend only, without Docker:

```bash
cd backend
pip install uv
uv sync
cp .env.example .env                 # fill in keys
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Unit tests (pure logic — no DB/Qdrant/LLM required):

```bash
cd backend && uv run pytest tests --ignore=tests/integration
```

Integration tests need a disposable PostgreSQL — see below.

## Configuration

- **Backend** — non-secret defaults live in `backend/conf/conf.json`.
  Precedence: constructor args → environment variables → `backend/.env` →
  `conf.json` → field defaults. Secrets (`OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `JWT_SECRET`, S3 credentials, …) stay in the
  environment and must never be committed to `conf.json`. Set
  `APP_CONFIG_FILE=/path/to/conf.json` to use a different file.
- **Frontend** — `frontend/conf/conf.json` is imported at build time
  (`src/lib/config.ts`); `NEXT_PUBLIC_API_URL` and
  `NEXT_PUBLIC_API_PREFIX` override it. Rebuild the frontend after
  changing the JSON.

### LLM providers

- Set `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY`. For a self-hosted
  LiteLLM (or any compatible proxy), also set `OPENAI_BASE_URL` or
  `ANTHROPIC_BASE_URL` — the SDKs call the standard chat/completions and
  messages endpoints through it.
- A selected model is resolved to its provider by the server allowlist;
  one `NodeContext`/LLM client is built per request and shared by all
  workflow nodes.
- Embeddings are independent of the chat provider: `EMBEDDING_PROVIDER`
  is `openai` (needs `OPENAI_API_KEY`/`OPENAI_BASE_URL`) or `local` (no
  key, deterministic hash vectors for development).
- Chat validates citations before emitting token frames, so the client
  never receives ungrounded text.

### OAuth / local development

`frontend/conf/conf.json` has `oauth_enabled` (default `false`). It only
controls the login UI; it does **not** disable backend authorization.

Two local-development paths:

1. **Turn backend auth off** (simplest): run the backend with
   `AUTH_DISABLED=true` and `APP_ENV=development`. No token is needed;
   `get_principal` returns a fixed dev Principal. With
   `oauth_enabled=false` the UI works without Google. `create_app` refuses
   `AUTH_DISABLED=true` outside development.
2. **Keep auth on, use a dev token**: seed a tenant/user/membership, mint
   an access JWT with `app.auth.issue_access_token`, and put it in
   `frontend/.env.local` as `NEXT_PUBLIC_DEV_ACCESS_TOKEN=...`
   (git-ignored). The REST and SSE clients attach it automatically.

### Database

The compose stack runs its own PostgreSQL (`agentic` / `agentic`, database
`agentic_rag`) and publishes it on host port **5436**, leaving 5432–5434
free for other local servers. API/worker use `postgres:5432` inside the
compose network. For a backend run outside Docker, keep `DATABASE_URL`
pointed at `localhost:5436` and run `uv run alembic upgrade head`.

Do not run these migrations inside another application's database (for
example an existing `pingpong` DB): the migrations own schema `public`.

### Vector store

The compose stack includes Qdrant (`localhost:6333` / gRPC `6334`) and the
API/worker use `qdrant:6333` internally. Retrieval needs it; if Qdrant is
up but the collection is empty, chat returns the insufficiency message
rather than crashing. Document upload/ingestion is still a 501 stub, so the
collection stays empty until that path is wired.

### Model selection

`backend/conf/conf.json` exposes the selectable models through
`llm_models` (id, label, provider) and `llm_default_model`. If
`llm_models` is empty, only the configured
`llm_provider`/`*_chat_model` pair is selectable.

- `GET /api/v1/models` (authenticated) returns the allowlist and default.
- `POST /api/v1/chat` accepts an optional `model`; the backend rejects
  anything outside the allowlist with 400 and records
  `model_provider`/`model_name` on the `agent_runs` row.
- The chat UI renders a dropdown from that endpoint; if the catalogue
  cannot be loaded (auth not wired yet) it stays on the server default.

## Key technical decisions

> The ChatGPT consultation service was unavailable (repeated timeouts), so
> the decisions below were made directly and are recorded here for review.

| Area | Decision | Rationale |
|---|---|---|
| Dependency management | **uv** + `pyproject.toml` (`[tool.uv] package=false`) | Fastest resolver; app is not a distributable package |
| Python | 3.12 (Docker `python:3.12-slim`) | `StrEnum`, modern typing, stable asyncio |
| DB access | **SQLAlchemy 2.0 async + asyncpg**, Alembic migrations | Async fits FastAPI; Alembic autogenerate from declarative metadata |
| Agent orchestration | **LangGraph `StateGraph`** with `TypedDict` state | Serializable state → PostgresSaver checkpointing possible later; centralized orchestrator (no multi-agent swarm) |
| Streaming | `graph.astream(stream_mode="updates")` → service layer → **sse-starlette** `EventSourceResponse` | Node updates become `status` frames; answer tokens emitted only **after** citation validation (backend owns citation truth, §7.5) |
| Vector store | **Qdrant** with named vectors `dense` + `sparse`, payload indexes on `tenant_id/knowledge_base_id/chunk_set_id/active` | Tenant isolation enforced server-side in the query filter, never client-side only (§15.2) |
| Hybrid fusion | **RRF (k=60)** over dense + sparse ranks | Rank-based only — raw scores from the two channels are incomparable |
| Qdrant client | **AsyncQdrantClient** on the query path (FastAPI/LangGraph), sync `QdrantClient` in the RQ worker | Async client (stable since 1.6) keeps the event loop free; the worker is process-bound anyway |
| Qdrant access | Two clients from `indexing_factory`: `get_async_qdrant_client()` (retrieval) / `get_qdrant_client()` (indexing) | Clear boundary: reads never block, writes stay simple and synchronous in the worker |
| Sparse encoding | Deterministic BM25-style hash encoder (interface allows swapping in SPLADE) | No model weights needed for MVP; benchmark decides if SPLADE earns its cost |
| Job queue | **RQ** (not Celery) | Simple, Redis-native, sufficient for one `ingestion` queue with `job_id` idempotency |
| LLM abstraction | `BaseLLMClient` (`complete` / `stream` / `complete_json`), lazy provider imports | OpenAI + Anthropic SDKs behind one interface; tests import base types without either SDK installed |
| Embeddings | Provider interface + `LocalHashEmbeddingProvider` offline fallback | Tests and CI run without API keys |
| AuthN/AuthZ | Backend-signed HS256 access JWT + **deny-by-default routers**; one `Principal(user, tenant, roles)` per request, bound to repositories | Tenant/roles come from `memberships`, never from the client or the LLM; every query carries the ownership predicate |
| Document visibility | `scope ∈ {org, personal}`: org shared inside the tenant, personal owner-only (admins do not outrank it); "not yours" == 404 | No existence oracle; DB composite FKs are the last line of defence |
| Frontend | **Next.js App Router**, `fetch` + `ReadableStream` for SSE (not `EventSource`) | `EventSource` cannot POST; manual SSE frame parsing in `lib/stream.ts` |
| State management | **zustand** for chat store; plain `useState` for documents | Chat needs fine-grained incremental updates; documents are simple CRUD |
| Testing | 49 pure-logic unit tests + 29 integration tests against a real Postgres (auth, IDOR, visibility, chat, repo + DB constraints) | Authorization bugs are exactly the ones mocks hide |
| Model selection | Server-side allowlist (`llm_models` / `llm_default_model` in conf.json); chat validates and records provider+model on the run; UI dropdown from `GET /models` | The client can never pick an arbitrary model id |
| Quality gate | `DocumentQualityReport` runs **before** chunking; `requires_ocr` blocks indexing (§4.4) | Prevents tuned chunking from operating on bad extraction |
| Upload boundary | `validate_upload` rejects by size (25 MB), magic bytes (`%PDF-`), MIME and extension **before** storage; parsing never runs in the request handler | Quality assessment judges extraction fidelity — security validation is a separate gate |
| Chunk-set publish | **Publish-after-verify**: upsert new set → verify points written → retire old set → store `documents.active_chunk_set_id` (Postgres = source of truth) | Reprocessing can never leave a document without a queryable chunk set |
| Citation grounding | Two-stage: marker validity against an immutable registry **plus** claim-support — uncited claim sentences are dropped, and if too little supported content remains the answer falls back to an explicit insufficiency message | Stripping `[R99]` alone would leave the hallucinated claim standing |
| Chunking selection | Constraint-first: generate → evaluate (structural + semantic) → hard-constraint filter → rank; offline retrieval benchmark (Layer 3) calibrates (§5.4) | Never promote a strategy on LLM self-assessment alone |
| Routing | `low` → fast path, `high`/multi-hop → agentic, else standard (§15.1) | Agentic retrieval is **not** the default path |
| Versioning | Every `agent_run` records workflow/prompt/model/embedding/retrieval versions (§9.7) | Reproducibility — a result that can't be reproduced is an observability defect |

## API surface (v1)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/health`, `/api/v1/health/ready` | Liveness / readiness (public) |
| POST | `/api/v1/auth/google` | Google ID token → backend access JWT (public, invite-only) |
| GET | `/api/v1/models` | Selectable model allowlist + default (authenticated) |
| POST | `/api/v1/documents` | Multipart upload (`file` + `scope`); validation only today — storage/RQ wiring returns 501 |
| GET | `/api/v1/documents` | List visible documents (org + own personal) |
| GET/DELETE | `/api/v1/documents/{id}` | Visible docs only / soft-delete (gated by `can_manage`) |
| GET | `/api/v1/documents/{id}/chunks` | Visibility-gated chunk listing (content still a stub) |
| POST | `/api/v1/documents/{id}/quarantine` | Admin only, org documents; removes it from retrieval snapshots |
| GET/POST | `/api/v1/conversations` | Conversations (private per user) |
| GET/DELETE | `/api/v1/conversations/{id}` | Fetch / delete (private per user) |
| GET | `/api/v1/conversations/{id}/messages` | History (private per user) |
| POST | `/api/v1/chat` | SSE: `run_started → status* → token* → citation* → completed` (or `error`) |

## Authentication & tenancy (rag_auth integration)

Deny-by-default at the router level: only `/health*` and `/auth/google`
are public; every other route requires `Authorization: Bearer <JWT>`.

- **`app/auth.py`** — `Principal(user_id, tenant_id, roles)`; access JWT
  signed by the backend (HS256 pinned, `exp/iss/aud/sub/typ` required);
  all failures return one uniform 401. Revocation: a disabled user or a
  removed membership loses access within `auth_revocation_ttl_seconds`
  (checked on every request in tests).
- **Invite-only Google login** (`POST /auth/google`) — Google proves
  identity only; tenant/roles come from `memberships`. Unknown `google_sub`
  → 403, never auto-provisioned (domain auto-join deferred).
- **Visibility** — `documents.scope ∈ {org, personal}` (no DEFAULT):
  org = shared in tenant, personal = owner only (admin does NOT outrank
  it). Conversations/messages/runs are private per (tenant, user).
  "Not yours" and "does not exist" are both 404 — no existence oracle.
- **DB as last line of defence** (migration `0003_auth_tenancy`) —
  composite FKs `(owner, tenant) → memberships` and
  `(conversation, tenant, user) → conversations`; `scope` CHECK; even
  buggy code cannot write cross-tenant rows.
- **Retrieval snapshot per run** — `agent_runs.snapshot`
  (`{document_id: chunk_set_id}`) resolved from the caller's visible,
  active, non-quarantined documents; client `document_ids` only narrow it.
- **Seeding** — there is no auto-provisioning: insert `tenants`, `users`
  (`google_sub`), then `memberships` manually. Tenant ids must be
  UUID-formatted strings: the JWT `tid` claim is typed as `UUID` even
  though the column is `String(64)`.
- **Development bypass** — `AUTH_DISABLED=true` skips Bearer
  authentication and binds a fixed dev `Principal`
  (`DEV_USER_ID` / `DEV_TENANT_ID`, roles `member`+`admin`). It is only
  allowed when `APP_ENV=development`; `create_app` refuses to start
  otherwise. Repositories still apply visibility using that Principal.

### Running the integration suite (real Postgres)

```bash
cd backend
docker compose -f tests/integration/docker-compose.test.yml up -d --wait
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/ragtest \
  uv run pytest tests/integration -q   # 29 tests: auth, IDOR, visibility, models, chat, repos+DB constraints
```

If port 5433 is already taken, set `TEST_PG_PORT` and use the same port in
the DSN:

```bash
TEST_PG_PORT=55432 docker compose -f tests/integration/docker-compose.test.yml up -d --wait
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/ragtest \
  uv run pytest tests/integration -q
```

⚠️ The suite **drops schema `public`** of the target database — point
`TEST_DATABASE_URL` only at a disposable DB.

## Production review — round 1 (external technical review)

An external review surfaced issues that were triaged against the actual
code (the reviewer worked from abbreviated snippets, so some claims were
false positives — e.g. an alleged `NameError` in `should_continue` that did
not exist in the real code). Fixed in this round:

| Issue | Status |
|---|---|
| **Unbounded agent loop** (real): `plan_node` reset `iteration_count` and nothing incremented it during retries, so persistent INSUFFICIENT evidence looped forever | ✅ Fixed — counter increments once per evaluate round; `should_continue` bounded |
| `max_iterations` trusted from LLM plan output | ✅ Fixed — clamped to `settings.agent_max_retrieval_iterations` in planner and router |
| Chunk sets never deactivated on reprocess → stale + duplicate results | ✅ Implemented — publish-after-verify + `active_chunk_set_id` pointer (migration 0002); ⚠️ the worker does not pass `run_metadata` yet (see Known gaps) |
| Citation stripping left hallucinated claims standing | ✅ Fixed — claim-support check drops uncited claims, falls back to an explicit insufficiency message |
| No upload validation at the boundary | ✅ Fixed — size / magic bytes / MIME / extension gate (`upload_validation.py`) |
| Sync Qdrant client blocking the event loop on the query path | ✅ Fixed — `AsyncQdrantClient` for retrieval, sync client for the worker |

Recorded for the next round (P1 — needs design, not spot fixes):
ingestion state machine with per-run job identity, prompt-injection
defenses (untrusted content, tool allowlist), Langfuse redaction, and the
remaining integration-test cases (citation reorder, reprocessing, client
disconnect). ~~Authorization middleware / IDOR tests / per-run retrieval
snapshot~~ — done (rag_auth integration, see above).

## Status

Raw codebase per Proposal v1.1 — the core logic is written; the
auth/tenancy layer and document/conversation read paths are wired to
Postgres, while upload→storage→RQ and the chat workflow are still pending.

- ✅ Backend modules, Alembic migrations (0001–0003), Docker, 49 unit tests
- ✅ Auth boundary: Principal JWT, deny-by-default routers, tenancy schema, Principal-bound repositories
- ✅ 29 integration tests (auth/IDOR/visibility/models/chat/repo+DB constraints) — require a real Postgres
- ✅ Model selection: server allowlist, validated `model` on chat, recorded on the run, UI dropdown
- ✅ Chat workflow wired: LangGraph runs per request, SSE status/token/citation/completed, assistant message + run persisted
- ✅ Development auth bypass (`AUTH_DISABLED`, development-only guard)
- ✅ Frontend chat (SSE) + documents (upload/poll/delete) pages
- ✅ Production review round 1 changes: loop bound, publish-after-verify, citation grounding, upload gate, async Qdrant (worker wiring gaps below)
- ⏳ Upload endpoint is still a validation-only stub (501); the document service and RQ enqueue are not called from the API
- ⏳ Frontend Google OAuth wiring + Bearer headers on API calls (chat currently needs a token)
- ⏳ Run the full suite against live Postgres/Qdrant/Redis/S3 + LLM keys (provider calls are covered by fakes in CI)
- ⏳ P1 hardening: ingestion state machine, injection defenses
- ⏳ Langfuse activation, offline retrieval benchmark

Known gaps (verified in the current code, not exhaustive):

- `process_document` builds the domain `Document` without `run_metadata`,
  so the previous chunk set is never retired on reprocess.
- Sparse vectors are encoded at query time but never written at index
  time — hybrid retrieval currently degrades to dense.
- Deleting a conversation that has `agent_runs` violates the composite FK
  (no `ON DELETE`), so `DELETE /conversations/{id}` returns 500 after a chat.
- The delete endpoint sets `status=deleting` but never enqueues the cleanup
  job, so vectors and stored files are not removed.
- A client that disconnects before `completed` may leave its run in
  `running`; the finalizer is best-effort under cancellation.

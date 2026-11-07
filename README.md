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
│   │   ├── api/v1/                 # REST + SSE endpoints
│   │   ├── domain/                 # Entities & workflow state (no framework deps)
│   │   ├── ingestion/              # Parse → IR → quality → sections → chunk → embed → index
│   │   │   └── chunking/           # 9 strategies + evaluation + constraint-first selection
│   │   ├── agentic_rag/            # LangGraph workflow, prompts, retrieval, evaluation, validation
│   │   ├── infrastructure/         # LLM, embeddings, Qdrant, SQLAlchemy, S3, Langfuse
│   │   ├── services/               # Chat & document use-cases
│   │   └── workers/                # RQ ingestion jobs
│   ├── migrations/                 # Alembic
│   └── tests/                      # pytest (26 tests, no external services needed)
└── frontend/                       # Next.js 15 (App Router) + TypeScript
    └── src/
        ├── app/                    # /chat, /documents pages
        ├── components/             # (pages are self-contained for MVP)
        ├── lib/                    # api.ts (REST), stream.ts (SSE via fetch), types.ts
        └── store/                  # zustand chat store
```

## Quick start

```bash
docker compose up --build
# API docs:  http://localhost:8000/docs
# Frontend:  http://localhost:3000
```

Backend only, without Docker:

```bash
cd backend
pip install uv && uv sync            # or: pip install -e .
cp .env.example .env                 # fill in keys
alembic upgrade head
uvicorn app.main:app --reload
```

Tests (pure logic — no DB/Qdrant/LLM required):

```bash
cd backend && pytest
```

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
| Frontend | **Next.js App Router**, `fetch` + `ReadableStream` for SSE (not `EventSource`) | `EventSource` cannot POST; manual SSE frame parsing in `lib/stream.ts` |
| State management | **zustand** for chat store; plain `useState` for documents | Chat needs fine-grained incremental updates; documents are simple CRUD |
| Testing | pytest + `asyncio_mode=auto`, pure-logic unit tests first | Chunking, citation validation, quality gates and RRF are testable without infrastructure |
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
| GET | `/api/v1/health` | Liveness |
| POST | `/api/v1/documents` | Multipart upload → RQ ingestion job |
| GET/DELETE | `/api/v1/documents/{id}` | List / soft-delete |
| GET/POST | `/api/v1/conversations` | Conversations CRUD |
| GET | `/api/v1/conversations/{id}/messages` | History |
| POST | `/api/v1/chat` | SSE stream: `run_started → status* → token* → citation* → completed` |

## Production review — round 1 (external technical review)

An external review surfaced issues that were triaged against the actual
code (the reviewer worked from abbreviated snippets, so some claims were
false positives — e.g. an alleged `NameError` in `should_continue` that did
not exist in the real code). Fixed in this round:

| Issue | Status |
|---|---|
| **Unbounded agent loop** (real): `plan_node` reset `iteration_count` and nothing incremented it during retries, so persistent INSUFFICIENT evidence looped forever | ✅ Fixed — counter increments once per evaluate round; `should_continue` bounded |
| `max_iterations` trusted from LLM plan output | ✅ Fixed — clamped to `settings.agent_max_retrieval_iterations` in planner and router |
| Chunk sets never deactivated on reprocess → stale + duplicate results | ✅ Fixed — publish-after-verify + `active_chunk_set_id` pointer (migration 0002) |
| Citation stripping left hallucinated claims standing | ✅ Fixed — claim-support check drops uncited claims, falls back to an explicit insufficiency message |
| No upload validation at the boundary | ✅ Fixed — size / magic bytes / MIME / extension gate (`upload_validation.py`) |
| Sync Qdrant client blocking the event loop on the query path | ✅ Fixed — `AsyncQdrantClient` for retrieval, sync client for the worker |

Recorded for the next round (P1 — needs design, not spot fixes):
authorization middleware (`Principal` + tenant-scoped SQL + IDOR tests),
ingestion state machine with per-run job identity, prompt-injection
defenses (untrusted content, tool allowlist), retrieval snapshot per run
(`active_chunk_set_ids` resolved at run start), Langfuse redaction, and
the integration-test matrix (tenant isolation, citation reorder,
reprocessing, client disconnect).

## Status

Raw codebase per Proposal v1.1 — full logic with real SDK calls written but
not yet wired/run against live infrastructure:

- ✅ Backend modules, Alembic migrations (0001–0002), Docker, 43 passing unit tests
- ✅ Frontend chat (SSE) + documents (upload/poll/delete) pages
- ✅ Production review round 1: loop bound, chunk-set publish, citation grounding, upload gate, async Qdrant
- ⏳ Run against live Postgres/Qdrant/Redis/S3 + LLM keys (next step)
- ⏳ P1 hardening: auth boundary, ingestion state machine, injection defenses
- ⏳ Google OAuth wiring, Langfuse activation, offline retrieval benchmark

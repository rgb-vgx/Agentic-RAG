# Agentic RAG — Chatbot Hỏi đáp tài liệu

Hệ thống Hỏi đáp & Trí tuệ Tài liệu Thích ứng (Adaptive Document Intelligence
& Question Answering System). Triển khai theo `Proposal Design.md`
(Technical Design v1.1).

```
Agentic-RAG/
├── Proposal Design.md              # Thiết kế kỹ thuật v1.1 (EN)
├── Proposal Design (Tiếng Việt).md # Bản tiếng Việt, line-aligned
├── README.vi.md                    # Bản tiếng Việt của README (tệp này)
├── docker-compose.yml              # Postgres + Redis + Qdrant + MinIO + API + worker + frontend
├── backend/                        # Python 3.12 + FastAPI
│   ├── app/
│   │   ├── api/v1/                 # Endpoint REST + SSE (auth, documents, conversations, chat)
│   │   ├── auth.py                 # Principal, access JWT, Google verifier, kiểm tra revocation
│   │   ├── domain/                 # Entity & trạng thái workflow (không phụ thuộc framework)
│   │   ├── ingestion/              # Parse → IR → quality → sections → chunk → embed → index
│   │   │   └── chunking/           # 9 chiến lược + đánh giá + chọn theo ràng buộc
│   │   ├── agentic_rag/            # Workflow LangGraph, prompt, retrieval, đánh giá, validation
│   │   ├── infrastructure/         # LLM, embeddings, Qdrant, SQLAlchemy, S3, Langfuse
│   │   ├── services/               # Use-case chat & tài liệu
│   │   └── workers/                # Job ingest qua RQ
│   ├── migrations/                 # Alembic (0001–0003)
│   ├── conf/conf.json              # Cấu hình runtime không bí mật (env ghi đè)
│   └── tests/                      # 54 unit test (không cần infra) + integration/ (29, Postgres thật)
└── frontend/                       # Next.js 15 (App Router) + TypeScript
    ├── conf/conf.json              # Cấu hình frontend, bundle lúc build
    └── src/
        ├── app/                    # Trang /chat, /documents (tự chứa)
        ├── lib/                    # api.ts (REST), stream.ts (SSE qua fetch), types.ts
        └── store/                  # zustand chat store
```

## Khởi động nhanh

```bash
docker compose up --build
# API docs:  http://localhost:8000/docs
# Frontend:  http://localhost:3000
# Postgres:  localhost:5436 (agentic/agentic)  |  Qdrant: localhost:6333/6334
```

Hoặc dùng script hỗ trợ (dừng mà không đụng tới named volume). Xem
[Vận hành stack local](#vận-hành-stack-local-controlsh) để có đầy đủ lệnh:

```bash
./control.sh start   # build nếu cần + bật mọi thứ
./control.sh seed    # tenant/user dev cho AUTH_DISABLED (idempotent)
./control.sh stop    # hoặc: off
```

> `docker-compose.yml` dùng `backend/.env.example` làm env file. Điền
> `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID` và `JWT_SECRET`
> khác mặc định trước khi kỳ vọng ingest hoặc đăng nhập chạy được.

Chỉ chạy backend, không dùng Docker:

```bash
cd backend
pip install uv
uv sync
cp .env.example .env                 # điền key
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Unit test (logic thuần — không cần DB/Qdrant/LLM):

```bash
cd backend && uv run pytest tests --ignore=tests/integration
```

Integration test cần một PostgreSQL dùng một lần — xem bên dưới.

## Vận hành stack local (`control.sh`)

`control.sh` bọc `docker compose` và giữ nguyên trạng thái qua các lần
restart. Toàn bộ dữ liệu nằm trong named volume, nên dừng stack không bao giờ
xoá tài liệu, hội thoại, vector hay cache model embedding.

### Lệnh

| Lệnh | Chức năng |
|---|---|
| `./control.sh start` | `docker compose up -d --build`, sau đó đảm bảo bucket MinIO và — khi `AUTH_DISABLED=true` — seed tenant/user/membership dev. |
| `./control.sh stop` / `off` | Dừng mọi container. Volume và dữ liệu được giữ nguyên. |
| `./control.sh restart [service]` | Restart tất cả hoặc một service (`api`, `worker`, `frontend`, `tei`, …). Lệnh này **không** nạp lại env đã đổi; dùng `start` (hoặc `up -d`) sau khi sửa `.env`. |
| `./control.sh status` | `docker compose ps`. |
| `./control.sh logs [service]` | Theo dõi log, có thể chỉ một service. |
| `./control.sh seed` | Chèn idempotent tenant, user và membership dev dùng bởi `AUTH_DISABLED`. |
| `./control.sh bucket` | Tạo bucket `agentic-rag` trên MinIO nếu chưa có. |
| `./control.sh embeddings tei\|ollama` | Chuyển backend embedding và tạo lại `api` + `worker` (xem bên dưới). |

### Cổng

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API / Swagger | http://localhost:8000/docs |
| Qdrant dashboard | http://localhost:6333/dashboard |
| MinIO API / console | localhost:9000 / http://localhost:9002 |
| TEI (embeddings) | http://localhost:8080 |
| Postgres | localhost:5436 (`agentic` / `agentic`, db `agentic_rag`) |
| Redis | localhost:6379 |
| Ollama (trên host) | http://localhost:11434 |

Console MinIO nằm ở **9002** vì `127.0.0.1:9001` đang bị service khác dùng
(penpot trong môi trường này). Nếu máy bạn còn trống, đổi mapping lại trong
`docker-compose.yml`.

### Lưu trữ

| Volume | Nội dung |
|---|---|
| `agentic-rag_pgdata` | Postgres: users, documents, conversations, runs |
| `agentic-rag_qdrant_data` | Các collection vector (`chunks_vi`, `chunks_ollama`) |
| `agentic-rag_minio_data` | File gốc đã upload |
| `agentic-rag_tei_data` | Cache Hugging Face cho model embedding của TEI |

`stop` chỉ dừng container. Không chạy `docker compose down -v` trừ khi bạn
thực sự muốn xoá toàn bộ dữ liệu. `start` dùng lại đúng các volume đó, nên
chạy lại nhiều lần vẫn an toàn.

### Backend embedding: TEI (mặc định) và Ollama

Có hai encoder. Mỗi encoder ghi vào collection Qdrant riêng vì vector từ các
model khác nhau không so sánh được với nhau.

| Backend | Model | Endpoint | Collection |
|---|---|---|---|
| `tei` (mặc định) | `Adc05102002/bge-m3-vi-base`, 1024 chiều | `http://tei:80/v1` | `chunks_vi` |
| `ollama` | `bge-m3`, 1024 chiều | `http://host.docker.internal:11434/v1` | `chunks_ollama` |

```bash
./control.sh embeddings ollama   # dùng bge-m3 qua Ollama
./control.sh embeddings tei      # quay lại TEI chạy GPU
```

Lệnh này ghi lại `RAG_EMBEDDING_*` / `RAG_QDRANT_COLLECTION` trong `.env` ở
thư mục gốc (đã git-ignore) rồi tạo lại `api` + `worker`. Chuyển backend
**không** migrate vector: tài liệu phải được ingest lại (upload lại) mới
retrieve được theo backend mới. Service TEI vẫn chạy ở cả hai chế độ
(~1,4 GB VRAM); `docker compose stop tei` sẽ giải phóng.

Đo trên RTX 3060: TEI GPU ≈ 1,7 ms/text, Ollama ≈ 6,7 ms/text (đã warm,
batch 64). Cả hai chạy song song được với model chat `qwen3.5:9b` — tổng
khoảng 7,7 GB / 12 GB VRAM.

### Checklist lần chạy đầu

1. Pull các model Ollama mà stack dùng:
   ```bash
   ollama pull qwen3.5:9b
   ollama pull bge-m3
   ```
2. File `.env` ở thư mục gốc (đã git-ignore) cho phát triển local:
   ```
   AUTH_DISABLED=true
   RAG_EMBEDDING_PROVIDER=openai
   RAG_EMBEDDING_MODEL=Adc05102002/bge-m3-vi-base
   RAG_EMBEDDING_DIM=1024
   RAG_EMBEDDING_BASE_URL=http://tei:80/v1
   RAG_QDRANT_COLLECTION=chunks_vi
   ```
   Tuỳ chọn: proxy tương thích Anthropic (bật model `deepseek-v4.1-flash`):
   ```
   RAG_ANTHROPIC_BASE_URL=https://opencode.ai/inference/go/anthropic
   RAG_ANTHROPIC_AUTH_TOKEN=<token>
   RAG_ANTHROPIC_EXTRA_HEADERS={"x-opencode-session":"agentic-rag"}
   ```
   Proxy yêu cầu header `x-opencode-session` và Bearer auth; backend gửi cả
   hai. Coi token là bí mật — `.env` đã được git-ignore.
3. `./control.sh start` — build image, chạy Alembic, tạo bucket và seed
   principal dev khi `AUTH_DISABLED=true`.
4. Mở http://localhost:3000/documents, upload PDF/DOCX/TXT, đợi trạng thái
   `ready`, rồi hỏi trong `/chat`.

### Lỗi thường gặp

- **Upload trả 502 kèm lỗi FK** — principal dev chưa được seed. Chạy
  `./control.sh seed` (cần `AUTH_DISABLED=true` và `APP_ENV=development`).
- **Chat trả 503 "OpenAI is not configured"** — container không kết nối được
  Ollama. Kiểm tra `extra_hosts: host.docker.internal:host-gateway` và
  `OPENAI_BASE_URL=http://host.docker.internal:11434/v1`.
- **Ingest lỗi `batch size 64 > maximum allowed batch size 32`** — TEI phải
  chạy với `--max-client-batch-size 64` (đã đặt trong `docker-compose.yml`).
- **Chat trả "không đủ bằng chứng" sau khi đổi backend embedding** — vector
  của tài liệu nằm ở collection còn lại; ingest lại tài liệu.
- **`model '<name>' not found`** — `ollama pull <name>` và đối chiếu
  `GET /api/v1/models` với `backend/conf/conf.json`.
- **`restart` không áp dụng thay đổi env** — restart giữ nguyên env cũ của
  container; chạy `./control.sh start` hoặc `docker compose up -d <service>`.

## Cấu hình

- **Backend** — giá trị mặc định không bí mật nằm trong
  `backend/conf/conf.json`. Thứ tự ưu tiên: tham số constructor → biến môi
  trường → `backend/.env` → `conf.json` → giá trị mặc định của field. Bí mật
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `JWT_SECRET`, thông tin S3, …) chỉ
  nằm trong môi trường và không bao giờ được commit vào `conf.json`. Đặt
  `APP_CONFIG_FILE=/path/to/conf.json` để dùng file khác.
- **Frontend** — `frontend/conf/conf.json` được import lúc build
  (`src/lib/config.ts`); `NEXT_PUBLIC_API_URL` và `NEXT_PUBLIC_API_PREFIX`
  ghi đè lên. Phải build lại frontend sau khi sửa JSON.

### Nhà cung cấp LLM

- Đặt `OPENAI_API_KEY` và/hoặc `ANTHROPIC_API_KEY`. Với LiteLLM tự host
  (hoặc proxy tương thích), đặt thêm `OPENAI_BASE_URL` hoặc
  `ANTHROPIC_BASE_URL` — SDK sẽ gọi endpoint chat/completions và messages
  chuẩn qua đó.
- Model được chọn sẽ được resolve về provider qua allowlist phía server;
  mỗi request tạo một `NodeContext`/LLM client và dùng chung cho mọi node
  trong workflow.
- Embedding độc lập với provider chat: `EMBEDDING_PROVIDER` là `openai`
  (mọi endpoint tương thích OpenAI) hoặc `local` (không cần key, vector hash
  tất định cho phát triển). `EMBEDDING_BASE_URL` ghi đè `OPENAI_BASE_URL`
  dùng chung, nên chat có thể đi Ollama trong khi embedding đi TEI
  (`http://tei:80/v1` trong compose, `http://localhost:8080/v1` ngoài Docker).
- Stack local phục vụ `Adc05102002/bge-m3-vi-base` (1024 chiều) qua
  `text-embeddings-inference`, và chat qua Ollama (`qwen3.5:9b`). Model
  "thinking" cần `openai_reasoning_effort: "none"` (conf.json), nếu không nó
  sẽ dùng hết token budget cho reasoning và trả content rỗng.
- Chat kiểm tra citation trước khi phát token, nên client không bao giờ nhận
  văn bản không có căn cứ.

### OAuth / phát triển local

`frontend/conf/conf.json` có `oauth_enabled` (mặc định `false`). Cờ này chỉ
điều khiển UI đăng nhập; **không** tắt authorization phía backend.

Hai cách phát triển local:

1. **Tắt auth backend** (đơn giản nhất): chạy backend với
   `AUTH_DISABLED=true` và `APP_ENV=development`. Không cần token;
   `get_principal` trả về Principal dev cố định. Với `oauth_enabled=false`,
   UI chạy được mà không cần Google. `create_app` từ chối
   `AUTH_DISABLED=true` ngoài môi trường development.
2. **Giữ auth, dùng dev token**: seed tenant/user/membership, mint access JWT
   bằng `app.auth.issue_access_token`, rồi đặt vào `frontend/.env.local` với
   `NEXT_PUBLIC_DEV_ACCESS_TOKEN=...` (đã git-ignore). Client REST và SSE tự
   gắn token.

### Cơ sở dữ liệu

Stack compose chạy PostgreSQL riêng (`agentic` / `agentic`, database
`agentic_rag`) và publish ra host port **5436**, để trống 5432–5434 cho các
server local khác. API/worker dùng `postgres:5432` trong mạng compose. Nếu
chạy backend ngoài Docker, giữ `DATABASE_URL` trỏ tới `localhost:5436` và
chạy `uv run alembic upgrade head`.

Không chạy các migration này trong database của ứng dụng khác (ví dụ DB
`pingpong` đang có): migration sở hữu schema `public`.

### Vector store

Stack compose có Qdrant (`localhost:6333` / gRPC `6334`) và API/worker dùng
`qdrant:6333` nội bộ. Retrieval cần nó; nếu Qdrant chạy nhưng collection
rỗng, chat trả message thiếu bằng chứng chứ không crash. Collection được tạo
bởi job ingest đầu tiên, với số chiều dense lấy từ `embedding_dim` — đổi model
embedding cần collection mới (stack này dùng `chunks_vi` với 1024 chiều).

### Định dạng upload hỗ trợ

| Định dạng | Parser | Ghi chú |
|---|---|---|
| PDF | PyMuPDF | heading theo cỡ chữ / in đậm, bảng qua layout detection |
| DOCX | python-docx | giữ thứ tự body; heading từ style Word cộng heuristic đoạn in đậm "Chương/Phần/Mục/Điều" tiếng Việt; giữ bảng |
| TXT / MD | built-in | heading Markdown `#` |
| DOC | — | bị từ chối khi validate — lưu lại thành `.docx` (định dạng nhị phân cũ) |

### Chọn model

`backend/conf/conf.json` công bố các model được chọn qua `llm_models`
(id, label, provider) và `llm_default_model`. Nếu `llm_models` rỗng, chỉ cặp
`llm_provider`/`*_chat_model` đã cấu hình là chọn được.

- `GET /api/v1/models` (cần auth) trả về allowlist và model mặc định.
- `POST /api/v1/chat` nhận `model` tuỳ chọn; backend từ chối mọi model ngoài
  allowlist với 400 và ghi `model_provider`/`model_name` vào row `agent_runs`.
- UI chat render dropdown từ endpoint đó; nếu không tải được catalogue (auth
  chưa nối) thì giữ model mặc định của server.

## Quyết định kỹ thuật chính

> Dịch vụ tư vấn ChatGPT không khả dụng (timeout liên tục), nên các quyết
> định dưới đây được đưa ra trực tiếp và ghi lại để review.

| Lĩnh vực | Quyết định | Lý do |
|---|---|---|
| Quản lý dependency | **uv** + `pyproject.toml` (`[tool.uv] package=false`) | Resolver nhanh nhất; app không phải package phân phối |
| Python | 3.12 (Docker `python:3.12-slim`) | `StrEnum`, typing hiện đại, asyncio ổn định |
| Truy cập DB | **SQLAlchemy 2.0 async + asyncpg**, migration Alembic | Async phù hợp FastAPI; Alembic autogenerate từ metadata declarative |
| Điều phối agent | **LangGraph `StateGraph`** với state `TypedDict` | State serializable → có thể checkpoint bằng PostgresSaver sau này; orchestrator tập trung (không phải multi-agent swarm) |
| Streaming | `graph.astream(stream_mode="updates")` → service layer → **sse-starlette** `EventSourceResponse` | Cập nhật node thành frame `status`; token trả lời chỉ phát **sau** khi citation được validate (backend sở hữu chân lý citation, §7.5) |
| Vector store | **Qdrant** với named vector `dense` + `sparse`, payload index trên `tenant_id/knowledge_base_id/chunk_set_id/active` | Cô lập tenant được thực thi phía server trong filter query, không chỉ phía client (§15.2) |
| Fusion hybrid | **RRF (k=60)** trên rank dense + sparse | Chỉ dựa trên rank — điểm thô của hai kênh không so sánh được |
| Qdrant client | **AsyncQdrantClient** trên đường query (FastAPI/LangGraph), `QdrantClient` sync trong worker RQ | Async client (ổn định từ 1.6) giữ event loop rảnh; worker vốn đã bound theo process |
| Truy cập Qdrant | Hai client từ `indexing_factory`: `get_async_qdrant_client()` (retrieval) / `get_qdrant_client()` (indexing) | Ranh giới rõ: đọc không block, ghi đơn giản và đồng bộ trong worker |
| Sparse encoding | Bộ mã hoá hash kiểu BM25 tất định (interface cho phép thay bằng SPLADE) | MVP không cần model weights; benchmark sẽ quyết định SPLADE có đáng chi phí |
| Hàng đợi job | **RQ** (không phải Celery) | Đơn giản, native Redis, đủ cho một queue `ingestion` với idempotency theo `job_id` |
| Trừu tượng LLM | `BaseLLMClient` (`complete` / `stream` / `complete_json`), import provider lazy | SDK OpenAI + Anthropic sau một interface; test import base type mà không cần cài SDK |
| Embeddings | Interface provider + fallback offline `LocalHashEmbeddingProvider` | Test và CI chạy không cần API key |
| AuthN/AuthZ | Access JWT HS256 do backend ký + **router deny-by-default**; một `Principal(user, tenant, roles)` mỗi request, bind vào repository | Tenant/role lấy từ `memberships`, không bao giờ từ client hay LLM; mọi query mang predicate sở hữu |
| Hiển thị tài liệu | `scope ∈ {org, personal}`: org chia sẻ trong tenant, personal chỉ chủ sở hữu (admin không vượt quyền); "không phải của bạn" == 404 | Không có existence oracle; composite FK ở DB là lớp phòng thủ cuối |
| Frontend | **Next.js App Router**, `fetch` + `ReadableStream` cho SSE (không dùng `EventSource`) | `EventSource` không POST được; parse frame SSE thủ công trong `lib/stream.ts` |
| Quản lý state | **zustand** cho chat store; `useState` cho documents | Chat cần cập nhật tăng dần chi tiết; documents là CRUD đơn giản |
| Testing | 54 unit test logic thuần + 29 integration test với Postgres thật (auth, IDOR, visibility, chat, repo + ràng buộc DB) | Bug authorization đúng là loại bug mà mock che mất |
| Chọn model | Allowlist phía server (`llm_models` / `llm_default_model` trong conf.json); chat validate và ghi provider+model vào run; UI dropdown từ `GET /models` | Client không bao giờ chọn được model id tuỳ ý |
| Quality gate | `DocumentQualityReport` chạy **trước** chunking; `requires_ocr` chặn indexing (§4.4) | Ngăn chunking tinh chỉnh chạy trên dữ liệu extract kém |
| Ranh giới upload | `validate_upload` từ chối theo size (25 MB), magic bytes (PDF `%PDF-`, DOCX `PK\x03\x04`), MIME và extension **trước** khi lưu; parsing không bao giờ chạy trong request handler. `.doc` cũ bị từ chối với message "save as .docx" | Quality assessment đánh giá độ trung thực extract — validate bảo mật là cổng riêng |
| Publish chunk-set | **Publish-after-verify**: upsert set mới → xác nhận point đã ghi → retire set cũ → lưu `documents.active_chunk_set_id` (Postgres = nguồn sự thật) | Reprocess không bao giờ để tài liệu không có chunk set query được |
| Citation grounding | Hai tầng: marker hợp lệ so với registry bất biến **cộng** claim-support — câu khẳng định không có citation bị loại, nếu còn quá ít nội dung được hỗ trợ thì trả message thiếu bằng chứng rõ ràng | Chỉ xoá `[R99]` sẽ để lại câu hallucination |
| Chọn chunking | Constraint-first: generate → evaluate (structural + semantic) → lọc hard-constraint → xếp hạng; benchmark retrieval offline (Layer 3) hiệu chỉnh (§5.4) | Không bao giờ promote một chiến lược chỉ dựa trên LLM tự đánh giá |
| Routing | `low` → fast path, `high`/multi-hop → agentic, còn lại standard (§15.1) | Agentic retrieval **không** phải đường mặc định |
| Versioning | Mỗi `agent_run` ghi version workflow/prompt/model/embedding/retrieval (§9.7) | Khả năng tái lập — kết quả không tái lập được là lỗi observability |

## Bề mặt API (v1)

| Method | Path | Ghi chú |
|---|---|---|
| GET | `/api/v1/health`, `/api/v1/health/ready` | Liveness / readiness (public) |
| POST | `/api/v1/auth/google` | Google ID token → access JWT của backend (public, invite-only) |
| GET | `/api/v1/models` | Allowlist model chọn được + mặc định (cần auth) |
| POST | `/api/v1/documents` | Multipart upload (`file` + `scope` + `knowledge_base_id` tuỳ chọn); nhận PDF, DOCX, TXT/MD, lưu vào MinIO và enqueue job ingest RQ |
| GET | `/api/v1/documents` | Liệt kê tài liệu được thấy (org + personal của mình) |
| GET/DELETE | `/api/v1/documents/{id}` | Chỉ tài liệu được thấy / soft-delete (gated bởi `can_manage`) |
| GET | `/api/v1/documents/{id}/chunks` | Liệt kê chunk có kiểm soát visibility (nội dung vẫn là stub) |
| POST | `/api/v1/documents/{id}/quarantine` | Chỉ admin, tài liệu org; loại khỏi snapshot retrieval |
| GET/POST | `/api/v1/conversations` | Hội thoại (riêng theo user) |
| GET/DELETE | `/api/v1/conversations/{id}` | Lấy / xoá (riêng theo user) |
| GET | `/api/v1/conversations/{id}/messages` | Lịch sử (riêng theo user) |
| POST | `/api/v1/chat` | SSE: `run_started → status* → token* → citation* → completed` (hoặc `error`) |

## Xác thực & tenancy (tích hợp rag_auth)

Deny-by-default ở cấp router: chỉ `/health*` và `/auth/google` là public;
mọi route khác yêu cầu `Authorization: Bearer <JWT>`.

- **`app/auth.py`** — `Principal(user_id, tenant_id, roles)`; access JWT do
  backend ký (pin HS256, bắt buộc `exp/iss/aud/sub/typ`); mọi lỗi trả cùng
  một 401. Revocation: user bị disable hoặc membership bị xoá sẽ mất quyền
  trong `auth_revocation_ttl_seconds` (kiểm tra mỗi request trong test).
- **Đăng nhập Google invite-only** (`POST /auth/google`) — Google chỉ chứng
  minh danh tính; tenant/role lấy từ `memberships`. `google_sub` lạ → 403,
  không bao giờ tự tạo (domain auto-join để sau).
- **Visibility** — `documents.scope ∈ {org, personal}` (không có DEFAULT):
  org = chia sẻ trong tenant, personal = chỉ chủ sở hữu (admin KHÔNG vượt
  quyền). Conversation/message/run riêng theo (tenant, user). "Không phải
  của bạn" và "không tồn tại" đều là 404 — không có existence oracle.
- **DB là lớp phòng thủ cuối** (migration `0003_auth_tenancy`) — composite FK
  `(owner, tenant) → memberships` và `(conversation, tenant, user) →
  conversations`; CHECK cho `scope`; code có bug cũng không ghi được row
  chéo tenant.
- **Snapshot retrieval mỗi run** — `agent_runs.snapshot`
  (`{document_id: chunk_set_id}`) resolve từ tài liệu mà caller được thấy,
  đang active, không bị quarantine; `document_ids` từ client chỉ thu hẹp.
- **Seeding** — không có auto-provisioning: chèn tay `tenants`, `users`
  (`google_sub`), rồi `memberships`. Tenant id phải là chuỗi dạng UUID: claim
  `tid` trong JWT được định kiểu `UUID` dù cột là `String(64)`.
- **Bypass cho development** — `AUTH_DISABLED=true` bỏ qua Bearer auth và
  bind `Principal` dev cố định (`DEV_USER_ID` / `DEV_TENANT_ID`, role
  `member`+`admin`). Chỉ được phép khi `APP_ENV=development`; `create_app`
  từ chối khởi động nếu khác. Repository vẫn áp dụng visibility theo
  Principal đó.

### Chạy bộ integration test (Postgres thật)

```bash
cd backend
docker compose -f tests/integration/docker-compose.test.yml up -d --wait
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/ragtest \
  uv run pytest tests/integration -q   # 29 test: auth, IDOR, visibility, models, chat, repos+DB constraints
```

Nếu port 5433 đã bị dùng, đặt `TEST_PG_PORT` và dùng cùng port trong DSN:

```bash
TEST_PG_PORT=55432 docker compose -f tests/integration/docker-compose.test.yml up -d --wait
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/ragtest \
  uv run pytest tests/integration -q
```

⚠️ Bộ test **drop schema `public`** của database đích — chỉ trỏ
`TEST_DATABASE_URL` vào DB dùng một lần.

## Đánh giá production — vòng 1 (review kỹ thuật bên ngoài)

Một review bên ngoài nêu các vấn đề và đã được đối chiếu với code thực tế
(reviewer làm việc từ snippet rút gọn nên có false positive — ví dụ cáo buộc
`NameError` trong `should_continue` không tồn tại trong code thật). Đã sửa
trong vòng này:

| Vấn đề | Trạng thái |
|---|---|
| **Vòng lặp agent không giới hạn** (thật): `plan_node` reset `iteration_count` và không có gì tăng nó trong retry, nên bằng chứng INSUFFICIENT kéo dài sẽ lặp mãi | ✅ Đã sửa — counter tăng một lần mỗi vòng evaluate; `should_continue` bị chặn |
| `max_iterations` tin từ output plan của LLM | ✅ Đã sửa — clamp theo `settings.agent_max_retrieval_iterations` trong planner và router |
| Chunk set không bao giờ bị deactivate khi reprocess → kết quả cũ + trùng | ✅ Đã triển khai — publish-after-verify + con trỏ `active_chunk_set_id` (migration 0002); ⚠️ worker nay đã truyền `run_metadata` |
| Stripping citation để lại câu hallucination | ✅ Đã sửa — claim-support check loại câu không có citation, fallback message thiếu bằng chứng |
| Không validate upload ở ranh giới | ✅ Đã sửa — cổng size / magic bytes / MIME / extension (`upload_validation.py`) |
| Qdrant client sync chặn event loop trên đường query | ✅ Đã sửa — `AsyncQdrantClient` cho retrieval, sync client cho worker |

Ghi lại cho vòng tiếp theo (P1 — cần thiết kế, không phải sửa điểm):
ingestion state machine với job identity theo run, phòng chống
prompt-injection (nội dung không tin cậy, tool allowlist), redaction Langfuse,
và các integration test còn lại (đổi thứ tự citation, reprocessing, client
disconnect). ~~Authorization middleware / IDOR test / snapshot retrieval theo
run~~ — đã xong (tích hợp rag_auth, xem trên).

## Trạng thái

Codebase theo Proposal v1.1 — luồng đầy đủ chạy được local:
upload → MinIO → RQ → parse/chunk/embed/index trong Qdrant → chat với
citation đã validate. Auth, tenancy và đường đọc tài liệu/hội thoại đã nối
với Postgres.

- ✅ Module backend, migration Alembic (0001–0003), Docker, 54 unit test
- ✅ Ranh giới auth: Principal JWT, router deny-by-default, schema tenancy, repository bind Principal
- ✅ 29 integration test (auth/IDOR/visibility/models/chat/repo+ràng buộc DB) — cần Postgres thật
- ✅ Chọn model: allowlist server, validate `model` khi chat, ghi vào run, dropdown UI
- ✅ Workflow chat đã nối: LangGraph chạy mỗi request, SSE status/token/citation/completed, lưu assistant message + run
- ✅ Bypass auth cho development (`AUTH_DISABLED`, guard chỉ development)
- ✅ Frontend chat (SSE) + documents (upload/poll/delete)
- ✅ Thay đổi từ review production vòng 1: giới hạn loop, publish-after-verify, citation grounding, upload gate, Qdrant async
- ✅ Upload → object storage → RQ đã nối (`POST /documents`), `DELETE` enqueue job dọn vector/file
- ✅ Vector dense + sparse được ghi lúc index; retrieval lọc theo snapshot visibility của caller
- ✅ Model local đã nối: Ollama `qwen3.5:9b` cho chat, TEI `bge-m3-vi-base` (1024 chiều) cho embedding
- ✅ Hỗ trợ upload PDF, DOCX, TXT/MD (legacy `.doc` bị từ chối)
- ⏳ Nối Google OAuth cho frontend (AUTH_DISABLED đủ cho local; login Google invite-only chưa có UI)
- ⏳ Hardening P1: ingestion state machine, phòng chống injection
- ⏳ Bật Langfuse, benchmark retrieval offline

Các gap đã biết (đã kiểm chứng trong code hiện tại, không đầy đủ):

- Xoá conversation đã có `agent_runs` vi phạm composite FK (không có
  `ON DELETE`), nên `DELETE /conversations/{id}` trả 500 sau khi chat.
- Client ngắt kết nối trước `completed` có thể để run ở trạng thái `running`;
  finalizer là best-effort khi bị cancel.

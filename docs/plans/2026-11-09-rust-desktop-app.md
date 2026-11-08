# Kế hoạch: Agentic RAG Desktop (Rust + Python)

- **Ngày**: 2026-11-09
- **Trạng thái**: Draft — đã cập nhật theo thảo luận kiến trúc
- **Phạm vi**: Windows + Linux
- **Mục tiêu**: App desktop local, UI giữ nguyên bản web hiện tại; dữ liệu tài liệu,
  index và hội thoại nằm trên máy user. Chat/embedding/rerank gọi API remote
  hoặc Ollama/LiteLLM nếu user chọn.

> Tài liệu này thay thế bản plan đầu tiên (port toàn bộ backend sang Rust).
> Hướng hiện tại: **Tauri/Rust shell + Python backend sidecar**, backend Python
> được tái sử dụng tối đa; port sang Rust là hạng mục sau, theo kiểu strangler.

---

## 1. Bối cảnh và ràng buộc

- UI hiện tại (Next.js) đã chốt thiết kế; yêu cầu giữ nguyên.
- Backend hiện tại: FastAPI + LangGraph, ~7.657 LOC Python, 79 file; test ~2.179 LOC.
- Hạ tầng hiện tại: Postgres, Redis + RQ, Qdrant, MinIO, Caddy.
- Embedding mặc định qwen3-embedding:8b (Ollama), rerank Qwen3-Reranker-4B qua
  service riêng; chat DeepSeek qua proxy Anthropic-compatible.
- Người dùng cuối không rành kỹ thuật: không được yêu cầu chạy Docker, sửa
  config, biết URL/model/dim.
- Windows và Linux là target trước mắt; macOS để sau.
- Dữ liệu tài liệu nên ở local. Chunk vẫn được gửi tới API chat/embedding/rerank,
  trừ khi user chọn Ollama/local.

---

## 2. Nhật ký quyết định

| # | Quyết định | Lý do |
|---|---|---|
| D1 | Không viết lại UI bằng Rust (Leptos/Dioxus) | Giữ đúng design; tiết kiệm 3–5 tuần; rủi ro lệch UI cao |
| D2 | Tauri 2 + web UI hiện tại (static export) | Bundle nhỏ, webview sẵn có, IPC trực tiếp với Rust |
| D3 | Giữ backend Python, Rust gọi qua localhost HTTP | Tái dùng pipeline/agent/citation; không port lại từ đầu |
| D4 | Python chạy như **sidecar** do Tauri quản lý | Một installer, user không phải khởi động service thủ công |
| D5 | Thêm **desktop profile** cho backend | Bỏ Postgres/Redis/MinIO/Qdrant server; dùng SQLite/filesystem/job in-process |
| D6 | Embedding/rerank mặc định gọi API remote | Không bundle torch/CUDA; giảm mạnh kích thước và độ phức tạp |
| D7 | Ollama là tùy chọn cho chat + embedding | User non-tech chỉ cần bật Ollama; app tự dò model |
| D8 | Rerank local không nằm trong MVP | Ollama không có endpoint rerank; GPU/VRAM phức tạp với máy user |
| D9 | Port Python → Rust là hạng mục sau | Có thể làm dần theo module; MVP không cần |
| D10 | Nếu port agent sang Rust: bỏ LangGraph, viết state machine | LangGraph không có SDK Rust; repo chỉ dùng 8 node + 1 vòng lặp, không checkpointer |

---

## 3. Các phương án đã cân nhắc

| Phương án | Cách chạy | Ưu | Nhược | Thời gian MVP |
|---|---|---|---|---|
| **A. Backend trung tâm** | Backend giữ nguyên trên server; Rust desktop gọi HTTP/SSE | Nhanh nhất, tái dùng 100% | Không local; tài liệu lên server; cần mạng/auth | 1–2 tuần |
| **B. Hybrid — Rust shell + Python sidecar** | Tauri bundle UI + Python backend chạy localhost | Dữ liệu local; giữ backend; một installer | Bundle 150–300 MB; Python startup; antivirus/ký số | 4–6 tuần |
| **C. Port toàn bộ sang Rust** | Rust core + LanceDB/Qdrant local | Single binary, không Python runtime | Port 7.6k LOC; parse PDF rủi ro; parity agent/citation | 6–10 tuần |

**Chọn B cho MVP.** Giữ C như lộ trình dài hạn nếu cần single binary.

---

## 4. Kiến trúc chọn: Tauri + Python sidecar

```
┌──────────────────────────────────────────────────────────────┐
│ Tauri app                                                    │
│                                                              │
│  WebView (Next.js static export — UI hiện tại)               │
│        │ invoke() / listen(event)                            │
│        ▼                                                     │
│  Rust desktop layer                                          │
│    ├─ tìm port trống, spawn Python sidecar (127.0.0.1)       │
│    ├─ sinh local token, truyền qua env                       │
│    ├─ health check, restart khi crash, kill khi thoát        │
│    ├─ proxy HTTP/SSE UI → sidecar                            │
│    ├─ file dialog, tray, auto-update, keychain               │
│    └─ log/rotate, crash report (opt-in)                      │
│                                                              │
│  Python sidecar (backend hiện tại, APP_ENV=desktop)          │
│    ├─ FastAPI + uvicorn (127.0.0.1, port động)               │
│    ├─ SQLite thay Postgres                                   │
│    ├─ filesystem thay MinIO/S3                               │
│    ├─ job in-process/thread thay Redis + RQ                  │
│    ├─ Qdrant local mode hoặc qdrant binary sidecar           │
│    ├─ ingestion/chunk/retrieval/agent/citation giữ nguyên    │
│    └─ HTTP clients → Chat / Embedding / Rerank API           │
└──────────────────────────────────────────────────────────────┘
```

### 4.1 Vì sao dùng localhost HTTP, không PyO3

- FastAPI/asyncio chạy ổn định trong process riêng; PyO3 nhúng interpreter dễ vỡ
  với GIL, event loop, native deps và packaging.
- Localhost HTTP cho phép test độc lập backend bằng `curl`/pytest.
- Rust kiểm soát vòng đời process, port, token; dễ restart/crash recovery.

### 4.2 Bảo mật local IPC

- Sidecar chỉ bind `127.0.0.1`, không `0.0.0.0`.
- Rust sinh token ngẫu nhiên mỗi lần chạy, truyền qua env; mọi request từ UI đi
  qua Rust proxy và mang token. Backend từ chối request thiếu token.
- Không bật CORS cho sidecar; webview không gọi trực tiếp.
- API key provider lưu OS keychain, không nằm trong webview/SQLite.

---

## 5. Desktop profile cho backend

Mục tiêu: **không sửa pipeline**, chỉ thay adapter hạ tầng.

| Hiện tại | Desktop profile | Ghi chú |
|---|---|---|
| Postgres + Alembic | SQLite (WAL) + migration desktop | SQLAlchemy async + aiosqlite; tránh kiểu dữ liệu Postgres-specific |
| MinIO/S3 | local filesystem adapter | Giữ interface storage hiện tại |
| Redis + RQ | job in-process + bảng `jobs` trong SQLite | Idempotent theo `job_id`; startup quét job treo |
| Qdrant server | `QdrantClient(path=...)` local mode, hoặc qdrant binary sidecar | Local mode đủ cho vài chục nghìn chunk; test sparse/named vectors |
| JWT/tenant | bỏ; local token Rust ↔ Python | Desktop một user |
| Caddy | bỏ; Tauri phục vụ UI | Không cần reverse proxy |
| Langfuse | tắt mặc định | Bật opt-in nếu có endpoint nội bộ |

Biến môi trường đề xuất:

```
APP_ENV=desktop
DATABASE_URL=sqlite+aiosqlite:///<app_data>/agentic.db
STORAGE_BACKEND=local
STORAGE_ROOT=<app_data>/files
QDRANT_MODE=local
QDRANT_PATH=<app_data>/qdrant
JOB_BACKEND=inline
LOCAL_API_TOKEN=<random>
```

### 5.1 Thay đổi code dự kiến (nhỏ)

- `Settings`: thêm `storage_backend`, `qdrant_mode/path`, `job_backend`.
- Storage: implement `LocalStorage` theo interface hiện có.
- Worker: gọi trực tiếp `ingestion_job` trong `asyncio.create_task`/thread pool;
  lưu trạng thái job vào SQLite.
- DB: migration riêng cho SQLite; tránh `JSONB`, `UUID` server-side defaults.
- Auth: middleware chấp nhận local token khi `APP_ENV=desktop`.
- Qdrant: factory chọn local mode; giữ nguyên payload schema.

---

## 6. Rust desktop layer

| Module | Trách nhiệm |
|---|---|
| `sidecar` | Tìm port, spawn/kill, health check, restart có backoff |
| `proxy` | Chuyển request UI → sidecar; stream SSE; timeout/cancel |
| `config` | Đọc/ghi profile; keychain; validate |
| `files` | File dialog, import, hash, mở thư mục dữ liệu |
| `updater` | Tauri updater, kiểm tra chữ ký |
| `tray` | Chạy nền, thoát hẳn, mở nhanh |
| `diagnostics` | Log xoay vòng, export log đã redact |

Không đưa logic RAG vào Rust ở MVP.

---

## 7. UX cho user non-tech

### 7.1 First-run wizard

1. **Chọn cách dùng AI**
   - `Dùng dịch vụ của công ty (khuyến nghị)` — base URL điền sẵn, chỉ nhập key.
   - `Dùng Ollama trên máy` — tự dò `127.0.0.1:11434`, liệt kê model đã cài.
   - `Tự cấu hình` — mở phần nâng cao.
2. **Kiểm tra kết nối** — hiển thị model chat/embedding, rerank bật/tắt, latency.
3. **Chọn thư mục tài liệu** — mặc định `Documents/AgenticRAG`.

### 7.2 Settings

Ba card: **Chat**, **Embedding**, **Rerank**.

- Preset: `Công ty`, `Ollama (máy này)`, `Tùy chỉnh`.
- Trạng thái: chưa cấu hình / đang kiểm tra / OK / lỗi.
- Nút **Kiểm tra kết nối**.
- Preset công ty: chỉ hiện ô API key; URL/model do app cấu hình.
- Ollama: tự dò, dropdown model, không hỏi URL.
- Tùy chỉnh: base URL, model, path, timeout, batch size; **không hỏi dim** —
  app probe một embedding ngắn hoặc dùng registry.

### 7.3 Ollama và rerank

- Ollama hỗ trợ chat và `/v1/embeddings`, **không có endpoint rerank**.
- Chọn Ollama → rerank tự động `Tắt (dense-only)`, UI ghi rõ.
- Không bắt user cấu hình rerank local trong MVP.

### 7.4 Đổi embedding model

- Mỗi `(provider, model, dim)` có `index_key` riêng.
- Đổi model → cảnh báo, đề nghị re-index; không trộn vector.
- Giữ index cũ đến khi re-index xong; cho phép xoá.

---

## 8. Đóng gói và phân phối

- Python: **PyInstaller one-folder** (tránh one-file/temp/AV), hoặc Nuitka.
- Rust/Tauri bundle: NSIS/MSI (Windows), AppImage/deb (Linux).
- Ước tính installer: **150–300 MB** mỗi OS.
- Windows: ký số để giảm SmartScreen/antivirus false positive.
- Update: Tauri updater; tải cả app + sidecar; kiểm tra chữ ký.
- Dữ liệu app:
  - Windows: `%APPDATA%\AgenticRAG`
  - Linux: `~/.local/share/agentic-rag`
- Gỡ app: giữ hoặc xoá app data theo lựa chọn; không để lại file ngoài.

---

## 9. Rủi ro và giảm thiểu

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| PyInstaller bị antivirus báo nhầm | Cao | Ký số; one-folder; test trên Windows Defender |
| Python khởi động chậm | TB | Splash + health check; preload; báo trạng thái |
| Sidecar crash/orphan | TB | Rust giám sát, restart có backoff, kill theo process group |
| Qdrant local mode thiếu tính năng | TB | Spike trước; fallback chạy qdrant binary sidecar |
| SQLite khác Postgres | TB | Test migration; tránh JSONB/UUID server defaults; integration test |
| WebView khác nhau Win/Linux | TB | pdf.js thay PDF iframe; test DOCX preview/zoom trên cả hai |
| Bundle lớn | TB | Không bundle model; tách optional components |
| Key/nội dung lộ trong log | Cao | Redact; không log payload; keychain |
| Update hỏng | TB | Rollback; kiểm tra chữ ký; staged rollout |

---

## 10. Lộ trình

### Track 1 — MVP desktop (khuyến nghị)

| Mốc | Nội dung | Ước tính | Hoàn thành khi |
|---|---|---|---|
| M0 | Tauri shell + static UI + spawn sidecar + health + local token + pdf.js | 3–4 ngày | App mở UI trên Windows/Linux, sidecar tự chạy |
| M1 | Desktop profile: SQLite + local storage + inline job + Qdrant local | 5–7 ngày | Import/index/chunk/preview chạy không Docker |
| M2 | Rust proxy HTTP/SSE + chat streaming + cancel | 3–5 ngày | Chat end-to-end qua sidecar, token stream mượt |
| M3 | Settings + first-run wizard + Ollama detection + re-index | 4–6 ngày | User non-tech cấu hình không cần terminal |
| M4 | Installer Win/Linux + updater + ký số + crash recovery | 4–6 ngày | Cài mới/nâng cấp/gỡ trên VM sạch |
| M5 | QA matrix, tài liệu, telemetry opt-in | 3–5 ngày | Checklist pass |

**Tổng: 4–6 tuần.**

### Track 2 — Port dần sang Rust (sau MVP, tùy chọn)

| Bước | Nội dung | Ước tính |
|---|---|---|
| R0 | Đánh giá parse PDF/DOCX Rust trên bộ fixture thật | 2–3 ngày |
| R1 | Port API/DB/storage sang axum + SQLx | 1–2 tuần |
| R2 | Port ingestion/chunking/index | 1–2 tuần |
| R3 | Port retrieval/rerank | 1 tuần |
| R4 | Port agent state machine + citation (bỏ LangGraph) | 1–2 tuần |
| R5 | Bỏ Python sidecar, đóng gói single binary | 3–5 ngày |

Tổng port: **6–10 tuần** (MVP), 9–14 tuần nếu giữ đầy đủ parity.

---

## 11. Đánh giá port Python → Rust

### 11.1 Số liệu

- 79 file Python, ~7.657 LOC; test ~2.179 LOC.
- Agent + citation: ~730 LOC.
- Embedding/rerank remote → không port ML runtime.

### 11.2 Độ khó

| Phần | Độ khó | Ước tính |
|---|---|---|
| API axum, DTO/serde, config, error | Dễ | 3–5 ngày |
| SQLx + schema/migrations | Dễ–TB | 3–5 ngày |
| LLM client + SSE | TB | 3–5 ngày |
| Embedding/rerank client | Dễ | 2–3 ngày |
| Parse PDF/DOCX | **Khó nhất** | 5–10 ngày |
| Quality/section/chunking | TB | 3–5 ngày |
| Qdrant client/filter/scroll | TB | 3–5 ngày |
| Retrieval/eval | TB | 3–5 ngày |
| Agent state machine | TB–Khó | 5–8 ngày |
| Citation validation (parity) | Khó | 3–5 ngày |
| Worker/cancel/crash recovery | TB | 3–5 ngày |
| Auth/tenant (nếu giữ) | TB | 3–5 ngày |
| Test parity/benchmark | Bắt buộc | 5–10 ngày |

### 11.3 LangGraph

- **Không có SDK Rust chính thức**; chỉ Python và JS/TS.
- Repo chỉ dùng: 8 node, 1 vòng lặp có điều kiện, `astream(stream_mode="updates")`,
  **không checkpointer/interrupt/human-in-loop**.
- Port sang Rust: enum `Node` + `AgentState` + vòng `loop/match` + event emit;
  ước tính 1–2 tuần, không cần thư viện graph.
- Nếu cần resume: lưu snapshot state vào SQLite sau mỗi node.

---

## 12. Tiêu chí nghiệm thu MVP

1. Cài trên Windows 10/11 và Ubuntu 22.04/24.04 sạch, mở app không cần terminal/Docker.
2. Import PDF/DOCX/TXT; preview/chunk hoạt động; dữ liệu trong app data.
3. Wizard cấu hình provider; key trong keychain; kiểm tra kết nối đúng.
4. Chat streaming + citation `[R1]` + source cards hoạt động.
5. Rerank lỗi/tắt → chat vẫn chạy dense-only với cảnh báo.
6. Đổi embedding model → cảnh báo + re-index, không trộn vector.
7. Không có key/nội dung tài liệu trong log mặc định.
8. Gỡ app không để lại dữ liệu ngoài app data (trừ keychain).

---

## 13. Câu hỏi cần chốt

1. Backend chạy **trung tâm** hay **sidecar local**? Nếu local, chấp nhận bundle 150–300 MB?
2. Có chấp nhận pdf.js thay PDF viewer native để đồng nhất Win/Linux?
3. Ollama preset chỉ cho chat + embedding, rerank tắt — ổn không?
4. Có cần auth/tenant trên desktop không? (Khuyến nghị: không)
5. Có cần OCR PDF scan trong MVP không?
6. Kênh phát hành/auto-update: server nội bộ hay tải installer thủ công?
7. Có cần single binary (bỏ Python) ngay, hay chấp nhận sidecar trước?

---

## 14. Phụ lục: prompt tham khảo ý kiến ngoài

### 14.1 Prompt cho ChatGPT

```
Tôi đang thiết kế một app desktop RAG nội bộ, cần bạn phản biện kiến trúc và
chỉ ra rủi ro. Bối cảnh:

- Windows + Linux là target; người dùng cuối không rành kỹ thuật.
- Hiện có backend Python FastAPI (~7.6k LOC) đã chạy được: ingestion PDF/DOCX,
  chunking, Qdrant, retrieval, agentic workflow (LangGraph 8 node, không
  checkpointer), citation validation. Hạ tầng hiện tại: Postgres, Redis/RQ,
  MinIO, Qdrant, Caddy.
- UI Next.js đã chốt design, yêu cầu giữ nguyên.
- Embedding/rerank gọi API remote; có thể cho chọn Ollama cho chat + embedding
  (Ollama không có rerank). Không bundle model/GPU.
- Dữ liệu tài liệu nên ở local; chunk vẫn gửi tới API provider.

Phương án đang cân nhắc:
A) Tauri/Rust shell + Python backend chạy sidecar localhost; backend thêm
   "desktop profile" (SQLite thay Postgres, filesystem thay MinIO, job
   in-process thay Redis/RQ, Qdrant local mode). Rust quản lý process, port,
   token, proxy HTTP/SSE, updater/keychain.
B) Port toàn bộ backend sang Rust (axum/sqlx, bỏ LangGraph, state machine).
C) Rust desktop gọi backend trung tâm (không local).

Hãy trả lời có cấu trúc:
1) Phản biện từng phương án; phương án nào nên chọn cho MVP 4-6 tuần?
2) Rủi ro lớn nhất của sidecar Python trên Windows (PyInstaller, antivirus,
   startup, crash/orphan, update) và cách giảm thiểu cụ thể.
3) Desktop profile nên thay Qdrant server bằng gì? So sánh qdrant local mode,
   qdrant binary sidecar, LanceDB cho <100k chunk.
4) Có nên giữ LangGraph trong sidecar hay thay bằng state machine?
5) Thiết kế UX cấu hình provider cho non-tech: preset công ty vs Ollama vs
   custom; cách tránh hỏi dim/model/URL.
6) Checklist nghiệm thu và những điểm dễ bị bỏ sót.
Đừng viết code; đưa ra quyết định và lý do.
```

### 14.2 Prompt cho Claude

```
Bạn là kiến trúc sư phần mềm khó tính. Hãy review kế hoạch app desktop RAG dưới
đây và chỉ ra chỗ sai, thiếu, hoặc lạc quan quá mức. Trả lời bằng tiếng Việt.

Bối cảnh repo hiện tại:
- Backend Python FastAPI ~7.657 LOC/79 file; test ~2.179 LOC.
- Ingestion PDF/DOCX/TXT → quality gate → section → chunk → embed → Qdrant.
- Retrieval dense + rerank service; agentic workflow LangGraph 8 node
  (resolve_context, analyze, route, plan, retrieve, evaluate, synthesize,
  validate_citations), 1 vòng lặp có điều kiện, không checkpointer; citation
  claim-support validation.
- Hạ tầng: Postgres, Redis/RQ, MinIO, Qdrant, Caddy. Chat DeepSeek qua proxy
  Anthropic-compatible; embedding qwen3-embedding:8b; rerank Qwen3-Reranker-4B.
- UI Next.js đã chốt, phải giữ nguyên.
- Target Windows + Linux; user non-tech; dữ liệu local; embedding/rerank gọi API
  remote, Ollama optional cho chat+embedding; không bundle model/GPU.
- LangGraph không có SDK Rust.

Kế hoạch đề xuất:
Tauri 2 + web UI hiện tại. Python backend chạy sidecar localhost do Rust quản lý
(port động, local token, health check, restart). Backend thêm desktop profile:
SQLite, filesystem storage, job in-process, Qdrant local mode. Rust proxy
HTTP/SSE, keychain, file dialog, updater. MVP 4-6 tuần. Port sang Rust để sau
(strangler), ước tính 6-10 tuần.

Hãy:
1) Đánh giá tính khả thi và ước lượng thời gian; chỗ nào lạc quan?
2) Liệt kê 5 rủi ro nghiêm trọng nhất kèm cách kiểm chứng sớm (spike).
3) So sánh sidecar Python vs port Rust cho mục tiêu single-binary, bảo mật,
   update, antivirus, trải nghiệm non-tech.
4) Rà soát desktop profile: SQLite vs Postgres, job in-process vs RQ, Qdrant
   local mode; chỉ ra lỗi tương thích cụ thể có thể gặp.
5) Thiết kế luồng khởi động/tắt/crash/update của sidecar và cách tránh orphan.
6) Đề xuất milestone M0-M5 đã đúng chưa; nếu không, sửa lại.
7) Nêu các quyết định còn thiếu trước khi bắt đầu code.
Không cần code; ưu tiên phản biện và tiêu chí go/no-go.
```

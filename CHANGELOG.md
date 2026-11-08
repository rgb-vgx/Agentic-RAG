# Changelog — Agentic RAG

Tài liệu này ghi lại các thay đổi của hệ thống theo góc nhìn **nghiệp vụ**:
mỗi mục trả lời ba câu hỏi — thay đổi để làm gì, tại sao phải thay đổi, và
sau khi thay đổi thì kết quả/kiểm chứng ra sao.

Phạm vi: từ commit `bdddc5a` (đưa hệ thống từ khung code thành luồng RAG chạy
được) đến các thay đổi chưa commit ở thời điểm 2026-11-08.

## Trạng thái hiện tại

Hệ thống chạy được end-to-end trên máy local:

```
upload (PDF/DOCX/TXT/MD) → MinIO → RQ worker
  → parse → quality gate → section → chunk → embed → Qdrant
  → chat: analyze → route → plan → retrieve → evaluate → synthesize
  → validate citations → SSE
```

- Chat model mặc định: `deepseek-v4.1-flash` qua proxy tương thích Anthropic
  (model local `qwen3.5:9b` đã tắt — xem mục Unreleased #3).
- Embedding mặc định: Ollama `qwen3-embedding:8b` (4096 chiều, query
  instruction, collection `chunks_qwen3_8b`); `bge-m3` là lựa chọn nhanh.
- Embedding thay thế: TEI `bge-m3-vi-base` (profile, tắt mặc định).
- Retrieval: dense-only; sparse vector vẫn được index nhưng tắt mặc định.
- 61 unit test (không cần infra) + 29 integration test (Postgres thật).

## Danh sách commit

| Commit | Thời điểm | Nội dung |
|---|---|---|
| `bdddc5a` | 2026-11-08 03:45 | Local model stack, upload pipeline, sửa retrieval/auth bugs |
| `f99d6c7` | 2026-11-08 04:09 | TEI GPU, switch embedding backend, hướng dẫn `control.sh` |
| `a7d898b` | 2026-11-08 04:22 | Hỗ trợ DOCX, từ chối `.doc` cũ |
| `fdd5e60` | 2026-11-08 04:34 | Sửa SSE CRLF, sửa logging |
| `417a29b` | 2026-11-08 20:51 | Preview client-side, chunk inspector, download |
| `af8a726` | 2026-11-08 21:09 | Sparse tokenizer tiếng Việt, phân trang chunk |
| `4ed8a7c` | 2026-11-08 21:25 | Embedding mặc định bge-m3, tắt sparse |
| _unreleased_ | 2026-11-08 | Markdown UI, giới hạn evidence cho synthesis |

---

## Unreleased

### 1. Chat UI render Markdown

**Nghiệp vụ.** Câu trả lời của trợ lý phải đọc được như văn bản: danh sách,
đậm, bảng, code block — thay vì hiển thị ký tự Markdown thô.

**Lý do.** UI cũ render `message.content` dưới dạng plain text trong bubble có
`white-space: pre-wrap`. Model trả Markdown (`**...**`, `* ...`, bảng), người
dùng thấy đầy dấu `*` và bố cục vỡ.

**Thay đổi.**

- Thêm `react-markdown` + `remark-gfm` (hỗ trợ bảng GFM).
- Assistant message render qua `<ReactMarkdown>`; user message giữ plain text.
- `[R1]` được chuyển thành badge `.citation-ref`; link ngoài mở tab mới.
- CSS cho heading, list, table, code, blockquote, `hr`.
- Không bật `rehype-raw` → HTML trong câu trả lời vẫn bị escape (an toàn).

**Kết quả.** Kiểm tra bằng browser: câu trả lời hiển thị list/bold, không còn
dấu `*`; citation `[R1]` hiển thị thành badge; source card phía dưới vẫn đúng.

### 2. Giới hạn evidence cho synthesis (sửa lỗi qwen trả lời sai)

**Nghiệp vụ.** Với model local, câu trả lời phải dựa trên bằng chứng đúng —
không được bỏ qua chunk xếp hạng cao nhất rồi trả lời từ tài liệu không liên
quan.

**Lý do (đã điều tra có bằng chứng).**
- Ollama chạy `qwen3.5:9b` với `num_ctx = 4096` (model khai báo 262k nhưng
  runtime mặc định là 4096).
- Prompt synthesis với 20 chunk: 17.897 ký tự ≈ **6.291 token** → vượt context.
- Ollama **cắt prompt từ đầu**, làm mất system instruction và các chunk
  `R1–R3` — đúng là các chunk chứa câu trả lời. Model chỉ còn thấy phần đuôi
  (các chunk Bộ luật không liên quan) nên trả lời sai.
- Endpoint OpenAI-compatible của Ollama **bỏ qua** trường `num_ctx`; chỉ
  native `/api/chat` (`options.num_ctx`) hoặc `OLLAMA_CONTEXT_LENGTH` của
  server mới đổi được.

Thí nghiệm cùng một prompt, chỉ đổi context:

| `num_ctx` | `prompt_eval_count` | Kết quả |
|---|---|---|
| 4096 | 2.050 (bị cắt) | sai: nói về “hình phạt bổ sung”, không có `0%`, không cite `R1` |
| 8192 | 6.291 | đúng: “Thuế suất … là **0%** [R1]” |
| 16384 | 6.291 | đúng |

Với top-8 evidence (≈2.437 token) qwen trả lời đúng ngay ở 4096.

**Thay đổi.**

- Thêm `synthesis_max_evidence_per_group = 8` (`conf.json`, settings).
- `WorkflowLimits.max_evidence_per_group`, truyền từ `build_node_context`.
- `synthesize_node` cắt evidence mỗi group trước khi build prompt.
- Ghi chú lý do trong README/README.vi.

**Kết quả.**

| Câu hỏi | Trước | Sau |
|---|---|---|
| Thuế suất thuế tiêu thụ đặc biệt đối với xăng? | sai, 31s, 7 citation | **đúng “0% [R1]”**, 6.7s |
| Án treo được áp dụng khi nào? | sai (nói về “tội bức tử”) | **đúng**, 8.5s, 2 citation |

### 3. Chuyển chat sang DeepSeek, tắt qwen và TEI

**Nghiệp vụ.** Chat mặc định phải cho câu trả lời đúng và ổn định; máy chỉ
giữ model embedding local.

**Lý do.**

- Qwen 9B local bị giới hạn context runtime 4096; prompt nhiều evidence bị
  cắt đầu (xem mục #2). Tăng `num_ctx` khả thi nhưng từ 128k trở lên đã
  offload CPU trên GPU 12 GB; 256k cần ~7,3 GB ngoài VRAM và rất chậm.
- DeepSeek trả lời đúng cùng retrieval (10,3s cho câu thuế xăng).
- Chỉ cần một model local duy nhất là `bge-m3` cho embedding → giảm VRAM
  xuống dưới 2 GB, không cần TEI.

**Thay đổi.**

- `conf.json`: allowlist chỉ còn `deepseek-v4.1-flash`, `llm_default_model`
  chuyển sang DeepSeek.
- TEI service chuyển sang profile `tei` (không tự start khi `control.sh
  start`); `control.sh embeddings tei` bật TEI, `embeddings ollama` dừng TEI.
- Unload qwen khỏi Ollama (model file vẫn giữ trên đĩa).
- README/README.vi cập nhật; cảnh báo dữ liệu rời máy.

**Kết quả.**

| Kiểm tra | Kết quả |
|---|---|
| `GET /api/v1/models` | chỉ trả `deepseek-v4.1-flash`, default là nó |
| Chat mặc định (câu thuế xăng) | **“0% [R1]”**, 10,4s |
| Ollama đang load | chỉ `bge-m3` (0,69 GB VRAM) |
| TEI container | dừng; VRAM tổng ~1,6 GB / 12 GB |

> Hệ quả nghiệp vụ: dữ liệu chat (câu hỏi + evidence) đi ra `opencode.ai`.
> Nếu cần giữ dữ liệu nội bộ, phải quay lại model local và chấp nhận giới hạn
> context/quality đã ghi ở trên.

### 4. Embedding qwen3-embedding:8b + query instruction (accuracy-first)

**Nghiệp vụ.** Độ chính xác là ưu tiên số 1 nên chọn encoder tốt nhất đo được
trên corpus pháp lý thật, chấp nhận chi phí VRAM/thời gian.

**Lý do.** Benchmark 850 chunk / 20 câu:

| Encoder | Top-1 | Top-5 | MRR | Tốc độ | VRAM | Dims |
|---|---|---|---|---|---|---|
| qwen3-embedding:8b + instruction | **18/20** | **20/20** | **0.950** | 130 ms/chunk | 5,7 GB | 4096 |
| qwen3-embedding:8b không instruction | 17/20 | 20/20 | 0.917 | 130 ms/chunk | 5,7 GB | 4096 |
| bge-m3 | 17/20 | 19/20 | 0.906 | 17 ms/chunk | 0,7 GB | 1024 |
| qwen3-embedding:4b | 16/20 | 20/20 | 0.900 | 77 ms/chunk | 3,4 GB | 2560 |

Instruction chỉ gắn cho query, không gắn cho document. 8B + instruction chỉ
còn 2 câu trượt (đều rank 2), trong khi bge-m3 có 1 câu rank 9.

**Thay đổi.**

- `embedding_query_instruction` + prefix `Instruct: ... / Query:` trong
  `OpenAIEmbeddingProvider` (query only).
- `control.sh embeddings qwen3` (model 8b, dim 4096, collection
  `chunks_qwen3_8b`, instruction); `ollama`/`tei` xoá instruction.
- Re-ingest 2 tài liệu vào collection mới.
- Ingestion bền hơn: `EMBEDDING_BATCH_SIZE=16`,
  `EMBEDDING_TIMEOUT_SECONDS=900`, retry 3 lần cho mỗi batch; Qdrant upsert
  chia **128 point/request** và timeout Qdrant 120s (trước đó 845 point ×
  4096 chiều trong một request làm Qdrant timeout và tài liệu bị đánh
  `failed`); `delete_document` chịu được collection chưa tồn tại.
- 3 test mới: instruction chỉ áp cho query, batch upsert, 63 test tổng.

**Kết quả.**

- Collection `chunks_qwen3_8b`: **850 point**, 4096 chiều; Bộ luật 845 chunk,
  Nghị quyết 5 chunk, cả hai `ready`.
- App path (có instruction): top-1 **18/20**, top-5 **20/20**, MRR **0.950**.
- Chat DeepSeek trả lời “0% [R1]” trong 8,7s.
- VRAM: qwen3-embedding:8b 5,73 GB; GPU ~6,3 GB / 12 GB.

### 5. Reranking Qwen3-Reranker-4B (transformers 4-bit)

**Nghiệp vụ.** Top-5 của retrieval đã 20/20 nhưng top-1 chỉ 18/20 — cần một
cross-encoder xếp lại ứng viên để đưa đúng điều khoản lên đầu.

**Lý do / điều tra runtime.**

- **Ollama** không có endpoint rerank (`/api/rerank`, `/v1/rerank` trả 404;
  docs và release mới nhất v0.40.15 không có). Model community
  `dengcao/Qwen3-Reranker-4B` chỉ chạy được qua chat/raw, không trả score ổn
  định.
- **llama.cpp** `/rerank` dùng rank pooling cho classifier reranker
  (bge-reranker) và trả score vô nghĩa với Qwen3-Reranker (causal LM chấm
  bằng xác suất token yes/no); hỗ trợ upstream vẫn là PR mở (#20009).
  Đã thử tự dựng prompt chính thức + `/completion` logprobs; tokenization
  giống hệt HF (85 token) nhưng logits `yes/no` vẫn phẳng với cả Q4 và Q8.
- **transformers** (official code) cho score dứt khoát: cặp relevant 0.998,
  irrelevant 0.0.

**Thay đổi.**

- Service `reranker/` (FastAPI + transformers 4-bit bitsandbytes,
  `POST /rerank`) chạy đúng prompt/model card. Cache model trong volume
  `reranker_cache` (~8 GB, tải lần đầu).
- Backend `HttpReranker` gọi `/rerank`; retrieval lấy **50 ứng viên** khi
  rerank bật, giữ **top 10**; fallback về thứ tự retrieval nếu service lỗi.
- Chạy cùng embedder 8B: batch 4, max length 4096, 4-bit (~4,7 GB).

**Kết quả (850 chunk, 20 câu).**

| Cấu hình | Top-1 | Top-5 | MRR |
|---|---|---|---|
| dense top-50 | 18/20 | 20/20 | 0.950 |
| **dense top-50 → rerank top-10** | **19/20** | **20/20** | **0.975** |

- Chỉ còn 1 câu trượt (rank 2); rerank 50 ứng viên ~9,3 s/query.
- Chat end-to-end với rerank: trả lời đúng, 18,6 s; VRAM 11,3 GB / 12 GB
  (embedder 5,7 + reranker 4-bit 4,7).

---

## 4ed8a7c — Embedding mặc định bge-m3, tắt sparse

**Nghiệp vụ.** Chất lượng tìm kiếm quyết định chất lượng câu trả lời. Hệ thống
phải dùng encoder tốt nhất có sẵn trên máy và không dùng kênh tìm kiếm làm
giảm kết quả.

**Lý do.** Benchmark trên corpus thật (850 chunk: 845 Bộ luật + 5 Nghị quyết,
20 câu hỏi có gold theo `Điều`):

| Cấu hình | Top-1 | Top-5 | MRR |
|---|---|---|---|
| `bge-m3-vi-base` (TEI) | 0/20 | 0/20 | 0.006 |
| **`bge-m3` official (Ollama), dense-only** | **18/20** | **20/20** | **0.935** |
| Sparse-only | 7/20 | 8/20 | 0.418 |
| RRF 0.5/0.5 | 10/20 | 18/20 | 0.681 |
| RRF 0.8/0.2 | 13/20 | 19/20 | 0.762 |

- Bản fine-tune `bge-m3-vi-base` gần như không dùng được trên corpus này.
- Sparse hash kém (7/20) và mọi mức RRF đều kéo dense xuống.

**Thay đổi.**

- Mặc định: `EMBEDDING_MODEL=bge-m3`, `EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1`,
  `QDRANT_COLLECTION=chunks_ollama`.
- TEI `bge-m3-vi-base` vẫn giữ làm lựa chọn (`./control.sh embeddings tei`).
- `retrieval_enable_sparse = false`; vector sparse vẫn được index để sau này
  đánh giá encoder tốt hơn (IDF/SPLADE).
- Re-ingest 2 tài liệu vào `chunks_ollama`: 850 point, 845 + 5 chunk.
- Cập nhật `conf.json`, compose, `.env.example`, `control.sh`, README/README.vi.

**Kết quả.** Dense retrieval 18/20 top-1, 20/20 top-5, MRR 0.935. Câu hỏi
thuế xăng: chunk Nghị quyết đứng rank 1 (0.752); với DeepSeek trả lời đúng
ngay, với qwen sau đó được sửa bằng giới hạn evidence.

---

## af8a726 — Sparse tiếng Việt + phân trang chunk

### 1. Sparse tokenizer Unicode-aware

**Nghiệp vụ.** Người dùng có thể gõ tiếng Việt có dấu hoặc không dấu; kết quả
tìm kiếm phải khớp cả hai.

**Lý do.** `SparseEncoder` dùng regex `[a-z0-9]+` (ASCII) nên cắt mất ký tự có
dấu:

```
"Nghỉ phép năm" → ['ngh', 'ph', 'p', 'n', 'm', 'c', ...]
```

Kênh sparse gần như vô nghĩa với tiếng Việt; benchmark hybrid không còn ý
nghĩa.

**Thay đổi.**

- Regex Unicode `\w+`, chuẩn hoá NFC + `casefold()`.
- Index **cả hai dạng** cho mỗi từ: có dấu (`nghỉ`, `phép`) và bỏ dấu
  (`nghi`, `phep`).
- Xử lý riêng `đ → d` (NFD không tách được `đ`).
- 3 unit test mới: giữ nguyên từ, không phân biệt dấu, xử lý `đ`.

**Kết quả.** Sparse top-1 trả đúng chunk Nghị quyết cho cả query có dấu và
không dấu (`Thue suat thue tieu thu...`). 61 test pass.

### 2. Phân trang chunk của tài liệu

**Nghiệp vụ.** Màn hình tài liệu phải xem được toàn bộ chunk mà hệ thống đã
index, không được cắt im lặng.

**Lý do.** Endpoint `/documents/{id}/chunks` chỉ scroll một lần, tối đa 200
point và báo `total` sai. Bộ luật 182 trang có 845 chunk nhưng UI chỉ thấy 200.

**Thay đổi.** Scroll theo batch đến hết (trần 5.000), trả `truncated`, sort
theo `section_id` (thứ tự Section Builder) thay vì heading text.

**Kết quả.** Bộ luật trả đủ 845 chunk, `truncated=false`, thứ tự `s0 → s463`.

---

## 417a29b — Preview client-side, chunk inspector, download

**Nghiệp vụ.** Người dùng cần:
1. Xem trước file gốc ngay trong UI.
2. Xem hệ thống đã chia chunk thế nào (debug chất lượng RAG).
3. Tải lại file gốc.

**Lý do.** Trước đó `/documents` chỉ có danh sách, trạng thái và nút xoá.
Endpoint chunk là stub; không có download; không thể kiểm tra chunk.

**Thay đổi.**

*Backend*
- `GET /documents/{id}/chunks`: đọc chunk của **active chunk set** từ Qdrant,
  filter `tenant_id + document_id + chunk_set_id + active`; visibility-gated.
- `GET /documents/{id}/download`: stream file gốc qua API (không lộ hostname
  MinIO), `Content-Disposition` hỗ trợ tên tiếng Việt (RFC 5987).
- Upload: trả chi tiết lỗi validation; log `filename`/`content_type`/lý do.
- Mở rộng MIME hợp lệ cho `.docx` (`application/zip`, `x-zip-compressed`,
  `wps-office.docx`, …).

*Frontend*
- Layout 3 cột: danh sách tài liệu / preview / chunk inspector.
- Chunk accordion: click mở nội dung chi tiết từng chunk.
- Preview client-side: DOCX bằng `docx-preview`, PDF bằng viewer native
  (blob URL), TXT/MD plain text. Không cần document server.
- Sửa vòng lặp polling: trang Documents gọi `GET /documents` **9.183 lần
  trong 2 phút** do effect vừa refresh vừa phụ thuộc `documents`.

*Ingestion (bug phát hiện khi xem chunk)*
- Section có subsection nhưng strategy thắng là `recursive`/
  `paragraph_grouping`/`semantic` (không đệ quy) → **mất toàn bộ nội dung
  con**. Bộ luật chỉ còn 53 chunk/15k ký tự.
- Sửa `ChunkingSelector._include_subsections`: luôn chunk subsection sau khi
  chọn strategy; thêm regression test.

**Kết quả.**

- Bộ luật sau fix: **845 chunk** (trước 53).
- Download: SHA-256 file tải về trùng file gốc.
- Preview: Nghị quyết render đầy đủ nội dung trong browser.
- 58 unit test pass.

**Quyết định bị đảo ngược.** Đã thử OnlyOffice Document Server (image 7 GB,
JWT tắt cho local) và gỡ bỏ: source clone không chứa submodule
(`sdkjs/web-apps/core/server` rỗng), và "preview" của OnlyOffice là cả editor
+ backend convert, không tách riêng được. Chọn preview client-side nhẹ hơn.

---

## fdd5e60 — Sửa SSE CRLF và logging

### 1. Chat UI không nhận được token

**Nghiệp vụ.** Chat phải stream câu trả lời ra UI.

**Lý do.** `sse-starlette` phát frame bằng **CRLF** (`\r\n\r\n`), frontend tách
frame bằng `"\n\n"` → không bao giờ khớp, UI không render token dù backend đã
`completed` và lưu câu trả lời.

**Thay đổi.** Tách frame bằng regex `/\r?\n\r?\n/`.

**Kết quả.** UI hiển thị câu trả lời + citation; đã kiểm tra bằng browser.

### 2. Logging ném AttributeError

**Lý do.** `logging.basicConfig(stream=True)` truyền bool vào `stream`, mọi
lần log ném `AttributeError: 'bool' object has no attribute 'write'`.

**Thay đổi.** Bỏ `stream=`, dùng stderr mặc định.

**Kết quả.** Log sạch, không còn traceback giả.

---

## a7d898b — Hỗ trợ DOCX

**Nghiệp vụ.** Tài liệu nghiệp vụ thực tế (nghị quyết, bộ luật, chính sách)
thường ở định dạng Word. Hệ thống phải ingest và trả lời được từ `.docx`.

**Lý do.** Trước đó chỉ nhận PDF/TXT/MD; upload `.docx` bị 415.

**Thay đổi.**

- `DocxParser` dùng `python-docx`: giữ thứ tự paragraph/table; heading từ
  style Word (`Heading N`, `Title`) và heuristic cho văn bản pháp lý Việt
  (`Chương/Phần/Mục/Điều` in đậm); trích bảng thành block `TABLE`.
- Validation: nhận MIME docx + magic bytes ZIP (`PK\x03\x04`), giới hạn
  25 MB; `.doc` cũ bị từ chối với message “save as .docx”.
- Frontend nhận `.docx`; thêm dependency `python-docx`; 5 test mới.

**Kết quả.** File Nghị quyết thật: 16 block, 2 heading, 2 bảng, quality 1.0,
5 chunk; chat trả lời đúng kèm citation. 54 test pass.

---

## f99d6c7 — TEI GPU, switch embedding, control.sh

**Nghiệp vụ.** Cần so sánh encoder embedding và vận hành stack bằng một lệnh,
dữ liệu không được mất khi dừng.

**Lý do.** TEI chạy CPU chậm; chưa có cách chuyển encoder; vận hành thủ công
nhiều bước.

**Thay đổi.**

- TEI image `86-1.9.2` (sm86/RTX 3060), `gpus: all`, `--dtype float16`,
  `--pooling cls`, `--max-client-batch-size 64`.
- `control.sh embeddings tei|ollama` ghi `RAG_EMBEDDING_*`/
  `RAG_QDRANT_COLLECTION` vào `.env` rồi recreate `api` + `worker`.
- `control.sh start/stop/restart/status/logs/seed/bucket`.
- README: mục vận hành chi tiết (lệnh, port, volume, first-run, troubleshooting).

**Kết quả.**

- TEI GPU ≈ 1,7 ms/text; Ollama ≈ 6,7 ms/text (warm, batch 64).
- 9B + TEI cùng lúc ≈ 7,7 GB / 12 GB VRAM.
- Sửa batch limit: pipeline gửi 64 text/request, TEI mặc định chỉ nhận 32.

---

## bdddc5a — Local model stack, upload pipeline

**Nghiệp vụ.** Đưa hệ thống từ khung đọc/ghi sang luồng RAG hoàn chỉnh: upload
tài liệu → ingest → chat có citation, chạy hoàn toàn bằng model local.

**Lý do (các lỗi chặn luồng).**
- `POST /documents` là stub 501 → Qdrant rỗng → chat luôn “không đủ bằng chứng”.
- Dev principal chưa seed → upload vi phạm FK `memberships`.
- RQ 3.0 không nhận `retry=3` (int).
- `RetrievedEvidence.chunk_id` khai báo `UUID` nhưng id thật là string
  (`<doc>-s0-c1`) → validation error khi có dữ liệu.
- Retrieval chỉ lọc `tenant_id`, **không áp snapshot visibility** → có thể
  trả tài liệu personal của người khác.
- Sparse vector không được ghi lúc index; worker không truyền `run_metadata`
  nên reprocess không retire chunk set cũ.

**Thay đổi.**

- Wire upload → `DocumentService` → MinIO → RQ; delete enqueue cleanup;
  boto3/redis chạy trong `asyncio.to_thread`.
- Seed dev tenant/user/membership (`backend/scripts/seed_dev.sql`,
  `./control.sh seed`).
- RQ `Retry(max=3, interval=[10,30,60])`.
- `chunk_id: str`; `snapshot_document_ids` trong `AgentState`; retrieve lọc
  theo snapshot; snapshot rỗng không fallback tìm toàn tenant.
- Sparse vector ghi lúc index; worker truyền `run_metadata`.
- Model local: `qwen3.5:9b` (chat), `qwen3-embedding:4b` (embedding lúc đó);
  `openai_reasoning_effort=none` cho model thinking.
- Anthropic-compatible proxy: Bearer auth, extra headers, `temperature` qua
  `extra_body` (SDK 2.6 bỏ tham số typed).
- Compose: `extra_hosts` tới Ollama; MinIO dùng image Chainguard, console
  9002 (9001 bị penpot chiếm).

**Kết quả.** Upload → `ready` → Qdrant point → chat trả lời kèm citation;
delete dọn vector/file. 49 test pass.

---

## Kết quả benchmark

### Retrieval — 850 chunk pháp lý, 20 câu hỏi

| Cấu hình | Top-1 | Top-5 | MRR |
|---|---|---|---|
| TEI `bge-m3-vi-base` | 0/20 | 0/20 | 0.006 |
| Ollama `bge-m3` dense | 18/20 | 20/20 | 0.935 |
| Ollama `bge-m3` dense + sparse (RRF) | 10–13/20 | 17–19/20 | 0.68–0.76 |
| Sparse-only | 7/20 | 8/20 | 0.418 |

### Qwen context — cùng prompt, chỉ đổi `num_ctx`

| `num_ctx` | `prompt_eval_count` | Kết quả |
|---|---|---|
| 4096 | 2.050 (bị cắt) | sai, không cite R1 |
| 8192 | 6.291 | đúng “0% [R1]” |
| 16384 | 6.291 | đúng |

### So sánh model chat (cùng retrieval)

| Model | Câu hỏi thuế xăng | Latency |
|---|---|---|
| `qwen3.5:9b` (trước fix context) | sai, 7 citation từ tài liệu không liên quan | 31s |
| `qwen3.5:9b` (sau cap 8) | đúng “0% [R1]” | 6.7s |
| `deepseek-v4.1-flash` | đúng “0% [R1]” | 10.3s |

> Lưu ý: DeepSeek đi qua proxy `opencode.ai` — dữ liệu rời khỏi máy. Chỉ dùng
> khi được phép.

---

## Cấu hình hiện tại

| Thành phần | Giá trị |
|---|---|
| Chat mặc định | `deepseek-v4.1-flash` qua Anthropic-compatible proxy |
| Chat local | đã tắt (`qwen3.5:9b`); model file vẫn trên đĩa |
| Embedding | Ollama `qwen3-embedding:8b`, 4096 chiều + query instruction |
| Reranker | Qwen3-Reranker-4B (transformers 4-bit), top-50 → top-10 |
| Qdrant collection | `chunks_qwen3_8b` (850 point) |
| Collection thay thế | `chunks_ollama` (bge-m3, nhanh) và `chunks_vi` (TEI, tắt mặc định) |
| Sparse | tắt (`retrieval_enable_sparse=false`), vector vẫn index |
| Evidence cho synthesis | tối đa 8 chunk/group |
| Upload | PDF, DOCX, TXT/MD; tối đa 25 MB; `.doc` bị từ chối |
| Preview | client-side: DOCX (`docx-preview`), PDF (native), TXT/MD |
| Auth | `AUTH_DISABLED=true` cho local (`APP_ENV=development`) |
| Dữ liệu | named volumes: `pgdata`, `qdrant_data`, `minio_data`, `tei_data` |

---

## Gap đã biết và hướng tiếp theo

1. **Reranker chưa có** — `RerankTool` hiện là no-op. 2/20 câu retrieval còn
   sai; reranker (bge-reranker) hoặc weighted RRF sau khi cải thiện sparse có
   thể xử lý.
2. **Sparse encoder cần IDF/stopwords/SPLADE** trước khi bật lại; hiện để
   tắt vì làm giảm chất lượng.
3. **Context của Ollama** — `num_ctx` bị endpoint OpenAI-compat bỏ qua. Nếu
   cần prompt dài hơn 8 chunk, phải dùng native `/api/chat` với
   `options.num_ctx` hoặc đặt `OLLAMA_CONTEXT_LENGTH` trên server.
4. **`bge-m3-vi-base`** — không dùng được trên corpus này; giữ lại chỉ để so
   sánh. Không nên làm mặc định.
5. **Xoá conversation sau khi chat trả 500** — composite FK tới `agent_runs`
   không có `ON DELETE`.
6. **Preview DOCX** — fidelity thấp hơn Word với layout phức tạp; `.doc`,
   `.xls`, `.pptx` không preview được.
7. **Qdrant client 1.20 vs server 1.12.4** — chỉ cảnh báo, nhưng nên đồng bộ
   version.
8. **Benchmark harness** chưa nằm trong repo; nên thêm `bench/` (datasets,
   eval_retrieval, eval_e2e, k6) để tái lập các số liệu trên.
9. **OnlyOffice** đã gỡ; image 7 GB và volume `agentic-rag_onlyoffice_data`
   còn trên máy, có thể xoá nếu không dùng.
10. **Dữ liệu rời máy khi chat**: DeepSeek đi qua `opencode.ai`. Đây là lựa
    chọn có chủ đích vì qwen local không đáp ứng được context/quality; nếu
    chính sách dữ liệu thay đổi, phải quay lại model local và giới hạn
    evidence nhiều hơn.
11. **Dọn model local không dùng**: `ollama rm qwen3.5:9b` (≈6,5 GB đĩa) và
    `docker rmi ghcr.io/huggingface/text-embeddings-inference:86-1.9.2`
    (nếu không bao giờ dùng lại TEI). Chưa chạy vì là thao tác xoá.
12. **Reranker chiếm VRAM đáng kể**: embedder 8B (5,7 GB) + reranker 4-bit
    (4,7 GB) ≈ 11,3 GB / 12 GB. Nếu cần dư VRAM, giảm batch hoặc tắt rerank
    (`RAG_RERANK_ENABLED=false`) và chấp nhận top-1 thấp hơn.
13. **Rerank latency** ~9,3 s/query cho 50 ứng viên; muốn nhanh hơn có thể
    giảm `rerank_candidates` (ví dụ 30) và đo lại trade-off.
14. **GGUF/llama.cpp dư thừa**: `ref/models/Qwen3-Reranker-4B.*.gguf` và
    image `ghcr.io/ggml-org/llama.cpp:server-cuda` không còn dùng; có thể xoá
    để giải phóng đĩa (chưa chạy).

## Ghi chú vận hành

- `./control.sh start` tự build, chạy Alembic, tạo bucket MinIO và seed
  principal dev khi `AUTH_DISABLED=true`.
- `./control.sh stop` không xoá volume; không chạy `docker compose down -v`
  trừ khi muốn mất toàn bộ dữ liệu.
- Đổi backend embedding phải **re-ingest** tài liệu vào collection mới;
  tài liệu cũ không tự migrate.
- Sau khi đổi `.env`, phải `docker compose up -d <service>` (recreate), không
  dùng `restart` (giữ env cũ).

# Benchmark: Docling vs Marker vs PyMuPDF trên PDF học thuật

- **Ngày**: 2026-11-09
- **File test**: `testdata/2611.05306v1.pdf` — arXiv “You don’t need long context”, 31 trang, A4, có text layer, bảng và công thức.
- **Mục tiêu**: đánh giá parser layout cho PDF khoa học/kỹ thuật trước khi tích hợp vào ingestion.

---

## 1. Kết quả hiện tại với PyMuPDF (baseline)

| Chỉ số | Giá trị |
|---|---|
| Pages / blocks | 31 / 2.567 |
| Headings phát hiện | 120 (sai: tác giả, `Figure N`, định nghĩa, bullet) |
| Sections | 2 (`Preamble`, dòng arXiv) → mất cấu trúc |
| Chunks | 144 |
| Quality | 0.71, cảnh báo header/footer lặp 97%, paragraph fragmentation |
| Bảng | `find_tables()` best-effort, không ổn định |
| Công thức | text glyph, không có LaTeX |

Nguyên nhân: parser coi mỗi line/span là block, heading dựa trên font size/bold nên caption và định nghĩa bị nhận nhầm; PDF có outline 26 mục nhưng parser bỏ qua.

---

## 2. Thiết lập benchmark

- GPU: RTX 3060 12 GB; reranker Qwen3-4B đang chiếm ~5 GB.
- Docling `2.149.0`, pipeline standard, `--tables --enrich-formula --device cuda`, ảnh export placeholder.
- Marker `2.1.0` (surya-ocr 0.23), `--mode balanced`, `--disable_image_extraction`, backend vLLM Docker.
- Marker cần `VLLM_GPU_MEMORY_UTILIZATION=0.45`, `SURYA_INFERENCE_PARALLEL=1`; cấu hình mặc định 0.85 OOM và full 31 trang lỗi 500.
- Output lưu tại:
  - `docs/benchmarks/2611.05306v1/docling.md`
  - `docs/benchmarks/2611.05306v1/marker.md`

---

## 3. Kết quả định lượng

| Tiêu chí | PyMuPDF | Docling | Marker |
|---|---|---|---|
| Thời gian 31 trang | vài giây | **47,9 s** convert (1:49 gồm load model) | **5:28** |
| RAM đỉnh | thấp | 3,43 GB | 1,50 GB + vLLM container |
| Heading | 120, sai nhiều | **34**, khớp TOC + References + appendix | 32, đúng mục nhưng level không đều |
| Section | 2 | **~26 mục TOC**, References tách riêng | ~26 mục, References tách riêng |
| Bảng | best-effort | **đúng cột, 115 dòng markdown** | 136 dòng nhưng **tách từng chữ thành cột** |
| Công thức | text glyph | LaTeX display, token bị tách khoảng trắng, không inline | **LaTeX sạch hơn**, có inline math và số phương trình |
| References | lẫn trong body | tách section rõ | tách section rõ |
| Text coherence | block/line vụn | đoạn văn liền mạch | đoạn văn liền mạch |
| Dependency | PyMuPDF | ~6 GB venv + model cache | ~5,8 GB venv + **Docker image vLLM 27,8 GB** |
| License | PyMuPDF AGPL/commercial | Code MIT; model theo license gốc | Code Apache-2.0; **model OpenRAIL-M**, hạn chế thương mại >5M USD |

### Bảng — ví dụ Table 1

Docling:

```
| Literary works - Work | Literary works - Words | ... | Model context window sizes - Model | ... |
| The Old Man and the Sea [21] | ∼ 27K | ∼ 37K | MiniMax M2.7 [31] | ∼ 150K | 200K |
```

Marker:

```
| The | Old | Man | and | the | Sea [21] | | ∼ 27K | ∼ 37K | | MiniMax | M2.7 | [31] | ...
```

### Công thức

Marker:

```latex
$$r_M = \frac{C_{\text{output},M}(16\text{K})}{C_{\text{output},M}(256\text{K})}
= \begin{cases} 0.2772 & \text{GLM 5.2,} \\ ... \end{cases} \quad (1)$$
```

Docling cũng xuất LaTeX nhưng token bị spaced (`C _ { o u t p u t , M }`), không có inline math.

---

## 4. Đánh giá

### Docling — khuyến nghị mặc định

Ưu điểm:

- Section/heading và References đúng, dùng được trực tiếp cho chunking.
- Bảng giữ cấu trúc cột — quan trọng với tài liệu kỹ thuật/pháp lý có bảng.
- Chạy in-process, không cần Docker/vLLM; tích hợp vào worker Python dễ.
- Code MIT; ít rủi ro license hơn PyMuPDF AGPL.
- Có JSON/DocTags với provenance (page, bbox) để map citation.

Nhược điểm:

- Công thức chưa đẹp bằng Marker (token spacing, thiếu inline math).
- Venv + model ~6 GB; lần đầu tải model, cần GPU/CPU đủ mạnh; không phù hợp bundle mặc định của desktop app.
- Tốc độ chậm hơn PyMuPDF nhiều (nhưng chấp nhận cho ingestion async).

### Marker — chỉ dùng khi công thức là ưu tiên

Ưu điểm:

- Công thức/inline math sạch nhất, có số phương trình.
- Text và References tốt, có page anchor.

Nhược điểm:

- Bảng kém hơn rõ rệt (word-split), không phù hợp tài liệu nhiều bảng.
- Phụ thuộc vLLM Docker (image 27,8 GB), cần NVIDIA runtime; mặc định OOM trên GPU 12 GB đang chạy reranker.
- Chậm (5:28 cho 31 trang) và vận hành nặng.
- Model weights OpenRAIL-M: free cho research/personal/startup <5M USD; commercial lớn phải mua license.

**Kết luận**: chọn **Docling** làm parser cho PDF khoa học/kỹ thuật phức tạp. Marker chỉ dùng như enrichment công thức tùy chọn, hoặc khi khách hàng chấp nhận hạ tầng vLLM.

---

## 5. Kế hoạch tích hợp Docling vào backend

### 5.1 Phân loại tài liệu

Thêm `document_type`:
- `legal`: giữ pipeline hiện tại (exact Điều/Khoản).
- `scientific`: Docling.
- `generic`: PyMuPDF fallback.

Heuristic nhận diện scientific:
- PDF có outline/TOC ≥ 3 mục, hoặc
- có `Abstract`, `References`, số mục `1.`, `2.1`, hoặc
- mật độ bảng/công thức cao.

Cho user chọn override trong UI upload (Auto / Legal / Scientific / Generic).

### 5.2 Adapter `DoclingParser`

Implement `BaseParser` mới:

```
DoclingParser.parse(bytes, document_id, filename)
  -> CanonicalDocument
```

- Chạy Docling trong worker process (async wrapper qua `asyncio.to_thread`).
- Map JSON Docling → `DocumentBlock`:
  - `section_header` → `BlockType.HEADING`, giữ level từ TOC.
  - `text` → `PARAGRAPH`.
  - `table` → `TABLE` + markdown/HTML.
  - `formula` → `PARAGRAPH`/`CODE` với LaTeX, metadata `content_type=formula`.
  - `caption` → metadata gắn section, không phải heading.
- Giữ `page_start/page_end`, `bbox`, `heading_path`.
- `References` đánh dấu `section_type=references`.

### 5.3 Chunking & retrieval

- Dùng heading từ Docling, chunk theo section 400–600 token.
- Không chunk lẫn References với body; filter `section_type=references` khỏi evidence mặc định.
- Bảng/công thức giữ nguyên trong một chunk, không cắt giữa bảng.
- Citation trả section + page; source card dùng chunk/page như hiện tại.
- Query khoa học không chạy exact legal lookup.

### 5.4 Metadata

- Title/authors từ block trang 1; `arxiv_id` nếu có `arXiv:...`; `document_year`.
- Không chạy `legal_metadata` cho scientific.
- Lưu `parser_name=docling`, `parser_version`, `document_type`.

### 5.5 Vận hành

- Dependency optional: `pip install .[docling]`; worker image variant `backend-docling`.
- Model cache volume riêng (`docling_cache`), không đóng vào image.
- `DOCLING_ENABLED`, `DOCLING_DEVICE=cuda|cpu`, timeout, fallback PyMuPDF.
- Với desktop Rust: Docling là **sidecar/componant tùy chọn**, chỉ tải khi user bật “PDF khoa học”; không đưa vào installer mặc định.

---

## 6. Việc cần làm tiếp

1. Spike `DoclingParser` + chạy lại file này, so section tree với TOC 26 mục.
2. Chạy Docling trên 5–10 PDF pháp lý thật (có bảng, scan) để chắc không regression.
3. Benchmark retrieval: 20 câu hỏi trên paper (RQ1, compaction, chi phí) và bộ câu pháp lý.
4. Quyết định có thêm Marker như optional formula enrichment hay không.
5. Chỉ khi ổn định mới đổi default parser; giữ PyMuPDF làm fallback.

---

## 7. Lệnh tái lập

```bash
# Docling
uv venv --python 3.12 /mnt/data/tmp/docling-bench
uv pip install --python /mnt/data/tmp/docling-bench/bin/python docling
/mnt/data/tmp/docling-bench/bin/docling convert testdata/2611.05306v1.pdf \
  --to md --to json --to doctags --tables --enrich-formula \
  --image-export-mode placeholder --device cuda --num-threads 8 \
  --output /tmp/docling-full

# Marker (cần Docker + NVIDIA runtime + image vllm/vllm-openai)
uv venv --python 3.12 /mnt/data/tmp/marker-bench
uv pip install --python /mnt/data/tmp/marker-bench/bin/python marker-pdf
SURYA_INFERENCE_BACKEND=vllm \
SURYA_INFERENCE_PARALLEL=1 \
VLLM_GPU_MEMORY_UTILIZATION=0.45 \
VLLM_MAX_MODEL_LEN=8192 \
/mnt/data/tmp/marker-bench/bin/marker_single testdata/2611.05306v1.pdf \
  --output_dir /tmp/marker-full --output_format markdown \
  --mode balanced --disable_image_extraction
```

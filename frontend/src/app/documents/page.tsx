"use client";

/** Documents page — list, client-side preview and chunk inspector. */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { DOCUMENTS_POLL_INTERVAL_MS } from "@/lib/config";
import type { ChunkItem, DocumentItem } from "@/lib/types";

const TEXT_TYPES = new Set(["txt", "md", "markdown"]);
const PREVIEW_TYPES = new Set(["docx", "pdf", "txt", "md", "markdown"]);

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<"org" | "personal">("org");

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewNonce, setPreviewNonce] = useState(0);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [textPreview, setTextPreview] = useState<string | null>(null);

  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [chunksError, setChunksError] = useState<string | null>(null);
  const [chunksTruncated, setChunksTruncated] = useState(false);
  const [openChunkId, setOpenChunkId] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const docxContainerRef = useRef<HTMLDivElement>(null);
  const objectUrlRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { items } = await api.listDocuments();
      setDocuments(items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Initial load only — refreshing here would retrigger this effect (the
  // dependency changes on every successful load) and hammer the API.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Status polling while anything is processing. This effect must not call
  // refresh() directly, or each poll would schedule the next one instantly.
  useEffect(() => {
    const hasPending = documents.some(
      (d) => d.status === "uploaded" || d.status === "processing"
    );
    if (!hasPending) return;
    const timer = setInterval(() => void refresh(), DOCUMENTS_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh, documents]);

  // Keep a valid selection as the list changes (upload/delete/poll).
  useEffect(() => {
    if (selectedId && documents.some((d) => d.id === selectedId)) return;
    const first =
      documents.find((d) => d.status === "ready" || d.status === "degraded") ??
      documents[0];
    setSelectedId(first?.id ?? null);
  }, [documents, selectedId]);

  // Chunks for the selected document.
  useEffect(() => {
    if (!selectedId) {
      setChunks([]);
      setChunksError(null);
      return;
    }
    let cancelled = false;
    setChunksLoading(true);
    setChunks([]);
    setChunksError(null);
    setChunksTruncated(false);
    setOpenChunkId(null);
    api
      .listChunks(selectedId)
      .then(({ items, truncated }) => {
        if (!cancelled) {
          setChunks(items);
          setChunksTruncated(Boolean(truncated));
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setChunksError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setChunksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const selectedDoc = documents.find((d) => d.id === selectedId) ?? null;
  const selectedType = selectedDoc?.file_type ?? "";

  // Client-side preview: DOCX via docx-preview, PDF via the native viewer,
  // TXT/MD as plain text. No document content leaves the browser.
  useEffect(() => {
    if (!selectedDoc || !PREVIEW_TYPES.has(selectedType)) {
      setPreviewLoading(false);
      setPreviewError(null);
      setPreviewUrl(null);
      setTextPreview(null);
      return;
    }

    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewUrl(null);
    setTextPreview(null);

    api
      .fetchDocumentBlob(selectedDoc.id)
      .then(async (blob) => {
        if (cancelled) return;
        if (selectedType === "pdf") {
          const url = URL.createObjectURL(blob);
          objectUrlRef.current = url;
          setPreviewUrl(url);
        } else if (selectedType === "docx") {
          const { renderAsync } = await import("docx-preview");
          const container = docxContainerRef.current;
          if (!container || cancelled) return;
          container.innerHTML = "";
          await renderAsync(blob, container, undefined, {
            className: "docx-preview",
            inWrapper: true,
            ignoreLastRenderedPageBreak: false,
          });
        } else {
          setTextPreview(await blob.text());
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setPreviewError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });

    return () => {
      cancelled = true;
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
    // selectedDoc identity changes on every poll; depend on its primitives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, selectedType, previewNonce]);

  async function uploadFiles(files: FileList | File[]) {
    setBusy(true);
    setError(null);
    try {
      for (const file of Array.from(files)) {
        await api.uploadDocument(file, scope);
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function downloadDocument(doc: DocumentItem) {
    try {
      await api.downloadDocument(doc.id, doc.filename);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function removeDocument(id: string) {
    try {
      await api.deleteDocument(id);
      setDocuments((prev) =>
        prev.map((d) => (d.id === id ? { ...d, status: "deleting" } : d))
      );
      setTimeout(() => void refresh(), 800);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="page documents-page">
      <h1>Documents</h1>

      {error && <div className="error-banner">{error}</div>}

      <div className="upload-bar">
        <label>
          Visibility:{" "}
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as "org" | "personal")}
            disabled={busy}
          >
            <option value="org">Org — shared in tenant</option>
            <option value="personal">Personal — only me</option>
          </select>
        </label>
        <div
          className={`upload-zone compact ${dragging ? "dragging" : ""}`}
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (e.dataTransfer.files.length)
              void uploadFiles(e.dataTransfer.files);
          }}
        >
          {busy
            ? "Uploading…"
            : "Drop PDF, DOCX or TXT files here, or click to browse"}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.md,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            style={{ display: "none" }}
            onChange={(e) => {
              if (e.target.files?.length) void uploadFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      <div className="doc-workspace">
        <aside className="panel doc-sidebar">
          <div className="panel-header">
            <span>Documents</span>
            <span className="doc-count">{documents.length}</span>
          </div>
          <div className="panel-body doc-sidebar-list">
            {documents.length === 0 && (
              <div className="empty">No documents yet.</div>
            )}
            {documents.map((doc) => (
              <button
                key={doc.id}
                className={`doc-item ${selectedId === doc.id ? "selected" : ""}`}
                onClick={() => setSelectedId(doc.id)}
              >
                <span className="doc-item-name">{doc.filename}</span>
                <span className="doc-item-meta">
                  <span className={`badge ${doc.status}`}>{doc.status}</span>
                  <span>{doc.file_type.toUpperCase()}</span>
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section className="panel doc-preview">
          <div className="panel-header">
            <span className="panel-title">
              {selectedDoc?.filename ?? "Preview"}
            </span>
            <div className="panel-actions">
              <button
                className="chunk-toggle"
                onClick={() => setPreviewNonce((n) => n + 1)}
                disabled={!selectedDoc}
              >
                Reload
              </button>
              {selectedDoc && (
                <button
                  className="download-btn"
                  onClick={() => void downloadDocument(selectedDoc)}
                >
                  Download
                </button>
              )}
              {selectedDoc && selectedDoc.status !== "deleting" && (
                <button
                  className="delete-btn"
                  onClick={() => void removeDocument(selectedDoc.id)}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
          <div className="panel-body preview-body">
            {!selectedDoc && (
              <div className="empty">Chọn một tài liệu để xem trước.</div>
            )}

            {selectedDoc && !PREVIEW_TYPES.has(selectedType) && (
              <div className="empty">
                Định dạng .{selectedType} chưa hỗ trợ preview trực tiếp — dùng
                nút Download để xem.
              </div>
            )}

            {selectedDoc && selectedType === "docx" && (
              <div ref={docxContainerRef} className="docx-preview-host" />
            )}

            {selectedDoc && selectedType === "pdf" && previewUrl && (
              <iframe
                className="pdf-preview"
                src={previewUrl}
                title={selectedDoc.filename}
              />
            )}

            {selectedDoc && TEXT_TYPES.has(selectedType) && (
              <pre className="text-preview">
                {textPreview ?? (previewLoading ? "Đang tải…" : "")}
              </pre>
            )}

            {selectedDoc && previewLoading && (
              <div className="preview-status">Đang tải preview…</div>
            )}
            {previewError && (
              <div className="error-banner preview-error">{previewError}</div>
            )}
          </div>
        </section>

        <aside className="panel doc-chunks">
          <div className="panel-header">
            <span>Chunks</span>
            <span className="doc-count">
              {chunks.length}
              {chunksTruncated ? "+" : ""}
            </span>
          </div>
          <div className="panel-body chunk-panel-body">
            {chunksLoading && <div className="empty">Đang tải chunk…</div>}
            {chunksError && <div className="error-banner">{chunksError}</div>}
            {!chunksLoading && !chunksError && chunks.length === 0 && (
              <div className="empty">
                Chưa có chunk — tài liệu có thể chưa index hoặc đang index bằng
                embedding backend khác.
              </div>
            )}
            {chunks.map((chunk, position) => {
              const open = openChunkId === chunk.chunk_id;
              return (
                <div key={chunk.chunk_id} className="chunk-entry">
                  <button
                    className="chunk-entry-head"
                    onClick={() =>
                      setOpenChunkId(open ? null : chunk.chunk_id)
                    }
                    aria-expanded={open}
                  >
                    <span className="ref">#{position + 1}</span>
                    <span className="heading">
                      {chunk.heading_path?.length
                        ? chunk.heading_path.join(" › ")
                        : "—"}
                    </span>
                    <span className="chunk-meta">
                      {chunk.page_start ? `p.${chunk.page_start}` : ""}
                      {chunk.strategy_name ? ` · ${chunk.strategy_name}` : ""}
                    </span>
                    <span className="chevron">{open ? "▾" : "▸"}</span>
                  </button>
                  {open && (
                    <div className="chunk-detail">
                      <pre className="chunk-content">{chunk.content}</pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </aside>
      </div>
    </div>
  );
}

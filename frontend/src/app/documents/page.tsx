"use client";

/** Documents page — upload, poll status, delete. */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { DOCUMENTS_POLL_INTERVAL_MS } from "@/lib/config";
import type { DocumentItem } from "@/lib/types";

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<"org" | "personal">("org");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const { items } = await api.listDocuments();
      setDocuments(items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Initial load + status polling while anything is processing
  useEffect(() => {
    void refresh();
    const hasPending = documents.some(
      (d) => d.status === "uploaded" || d.status === "processing"
    );
    if (!hasPending) return;
    const timer = setInterval(() => void refresh(), DOCUMENTS_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh, documents]);

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
    <div className="page">
      <h1>Documents</h1>

      {error && <div className="error-banner">{error}</div>}

      <div style={{ marginBottom: 12 }}>
        <label>
          Visibility:{" "}
          <select
            value={scope}
            onChange={(e) =>
              setScope(e.target.value as "org" | "personal")
            }
            disabled={busy}
          >
            <option value="org">Org — shared in tenant</option>
            <option value="personal">Personal — only me</option>
          </select>
        </label>
      </div>

      <div
        className={`upload-zone ${dragging ? "dragging" : ""}`}
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (e.dataTransfer.files.length) void uploadFiles(e.dataTransfer.files);
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

      <div className="doc-list">
        {documents.length === 0 && (
          <div className="empty">No documents yet.</div>
        )}
        {documents.map((doc) => (
          <div key={doc.id} className="doc-row">
            <div>
              <div>{doc.filename}</div>
              <div className="meta">
                {doc.file_type.toUpperCase()}
                {doc.quality_summary?.requires_ocr && " · OCR required"}
                {doc.quality_summary?.requires_manual_review &&
                  " · manual review"}
                {doc.error_message ? ` · ${doc.error_message}` : ""}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className={`badge ${doc.status}`}>{doc.status}</span>
              {doc.status !== "deleting" && (
                <button
                  className="delete-btn"
                  onClick={() => void removeDocument(doc.id)}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

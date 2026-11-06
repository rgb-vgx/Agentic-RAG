/** Shared API types mirroring backend response contracts. */

export type DocumentStatus =
  | "uploaded"
  | "processing"
  | "ready"
  | "degraded"
  | "failed"
  | "deleting"
  | "deleted";

export interface DocumentItem {
  id: string;
  filename: string;
  status: DocumentStatus;
  file_type: string;
  error_message?: string | null;
  quality_summary?: {
    extraction_quality: number;
    requires_ocr: boolean;
    requires_manual_review: boolean;
    warnings: string[];
  } | null;
}

export interface ConversationItem {
  id: string;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SourceReference {
  reference_id: string;
  chunk_id: string;
  document_id: string;
  page_start?: number | null;
  page_end?: number | null;
  heading_path: string[];
  snippet: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: SourceReference[];
  model_name?: string | null;
  streaming?: boolean;
}

/** SSE event payloads (mirrors backend app/api/v1/chat.py contract). */
export type StreamEvent =
  | { event: "run_started"; data: { run_id: string } }
  | { event: "status"; data: { node: string; update?: Record<string, unknown> } }
  | { event: "token"; data: { delta: string } }
  | { event: "citation"; data: SourceReference }
  | {
      event: "completed";
      data: { run_id: string; routing_path?: string; citations?: number };
    }
  | { event: "error"; data: { message: string } };

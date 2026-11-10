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

export interface ChunkItem {
  chunk_id: string;
  chunk_index: number;
  content: string;
  heading_path: string[];
  page_start?: number | null;
  page_end?: number | null;
  strategy_name?: string | null;
  section_id?: string | null;
  parent_chunk_id?: string | null;
}

export interface ConversationItem {
  id: string;
  title: string;
  title_source?: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface CurrentUser {
  user_id: string;
  tenant_id: string;
  email: string;
  roles: string[];
  avatar_url?: string | null;
}

export interface IntegrationStatus {
  configured: boolean;
  enabled: boolean;
  masked_key?: string | null;
  updated_at?: string | null;
}

export interface ModelOption {
  id: string;
  label: string;
  provider: string;
}

export interface SourceReference {
  reference_id: string;
  chunk_id: string;
  document_id: string;
  page_start?: number | null;
  page_end?: number | null;
  heading_path: string[];
  snippet: string;
  url?: string | null;
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
  | {
      event: "run_started";
      data: {
        run_id: string;
        model?: string;
        model_provider?: string;
        snapshot_document_ids?: string[];
      };
    }
  | { event: "status"; data: { node: string; update?: Record<string, unknown> } }
  | { event: "token"; data: { delta: string } }
  | { event: "citation"; data: SourceReference }
  | {
      event: "approval";
      data: {
        action: "delete_document";
        document_id: string;
        filename: string;
        message?: string;
      };
    }
  | {
      event: "completed";
      data: { run_id: string; routing_path?: string; citations?: number };
    }
  | { event: "error"; data: { message: string } };

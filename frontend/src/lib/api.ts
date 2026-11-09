/** REST client for the Agentic RAG API. */

import { authHeaders } from "./auth";
import { API_PREFIX } from "./config";
import type {
  ChunkItem,
  ConversationItem,
  CurrentUser,
  DocumentItem,
  IntegrationStatus,
  ModelOption,
} from "./types";

const PREFIX = API_PREFIX;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${PREFIX}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      const { clearAccessToken } = await import("./oauth");
      clearAccessToken();
      if (window.location.pathname !== "/login") {
        window.location.assign("/login");
      }
    }
    const body = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  // Health
  health: () => request<{ status: string }>("/health"),

  me: () => request<CurrentUser>("/auth/me"),

  // Documents
  listDocuments: (knowledgeBaseId?: string) =>
    request<{ items: DocumentItem[]; total: number }>(
      `/documents${knowledgeBaseId ? `?knowledge_base_id=${knowledgeBaseId}` : ""}`
    ),

  uploadDocument: async (file: File, scope: "org" | "personal" = "org") => {
    const form = new FormData();
    form.append("file", file);
    form.append("scope", scope);
    const response = await fetch(`${PREFIX}/documents`, {
      method: "POST",
      headers: authHeaders(),
      body: form, // fetch sets multipart boundary itself
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as {
        detail?: string;
      } | null;
      throw new Error(
        body?.detail ?? `${response.status} ${response.statusText}`
      );
    }
    return (await response.json()) as DocumentItem;
  },

  fetchDocumentBlob: async (id: string) => {
    const response = await fetch(`${PREFIX}/documents/${id}/download`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return response.blob();
  },

  listChunks: (id: string) =>
    request<{
      items: ChunkItem[];
      total: number;
      truncated?: boolean;
      chunk_set_id?: string | null;
    }>(`/documents/${id}/chunks`),

  downloadDocument: async (id: string, filename: string) => {
    const response = await fetch(`${PREFIX}/documents/${id}/download`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },

  deleteDocument: (id: string) =>
    request<void>(`/documents/${id}`, { method: "DELETE" }),

  // Models
  listModels: () =>
    request<{ items: ModelOption[]; default: string }>("/models"),

  // Integrations (per user)
  getTavily: () => request<IntegrationStatus>("/integrations/tavily"),

  saveTavily: (apiKey: string | null, enabled: boolean) =>
    request<IntegrationStatus>("/integrations/tavily", {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey, enabled }),
    }),

  deleteTavily: () =>
    request<void>("/integrations/tavily", { method: "DELETE" }),

  testTavily: (apiKey?: string) =>
    request<{ ok: boolean }>("/integrations/tavily/test", {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey || null }),
    }),

  // Conversations
  listConversations: () =>
    request<{ items: ConversationItem[]; total: number }>("/conversations"),

  createConversation: (title = "New conversation") =>
    request<ConversationItem>("/conversations", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),

  renameConversation: (id: string, title: string) =>
    request<ConversationItem>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

  deleteConversation: (id: string) =>
    request<void>(`/conversations/${id}`, { method: "DELETE" }),

  autoTitleConversation: (id: string, model?: string) =>
    request<ConversationItem>(`/conversations/${id}/auto-title`, {
      method: "POST",
      body: JSON.stringify({ model: model || null }),
    }),

  listMessages: (conversationId: string) =>
    request<
      Array<{
        id: string;
        role: "user" | "assistant";
        content: string;
        sources: unknown[];
        model_name?: string | null;
      }>
    >(`/conversations/${conversationId}/messages`),
};

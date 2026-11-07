/** REST client for the Agentic RAG API. */

import { authHeaders } from "./auth";
import { API_PREFIX } from "./config";
import type { ConversationItem, DocumentItem, ModelOption } from "./types";

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
    const body = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  // Health
  health: () => request<{ status: string }>("/health"),

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
      throw new Error(`${response.status} ${response.statusText}`);
    }
    return (await response.json()) as DocumentItem;
  },

  deleteDocument: (id: string) =>
    request<void>(`/documents/${id}`, { method: "DELETE" }),

  // Models
  listModels: () =>
    request<{ items: ModelOption[]; default: string }>("/models"),

  // Conversations
  listConversations: () =>
    request<{ items: ConversationItem[]; total: number }>("/conversations"),

  createConversation: (title = "New conversation") =>
    request<ConversationItem>("/conversations", {
      method: "POST",
      body: JSON.stringify({ title }),
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

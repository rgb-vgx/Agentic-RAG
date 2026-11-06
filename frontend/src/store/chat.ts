"use client";

/** Chat state store (zustand) — messages, streaming status, conversation id. */

import { create } from "zustand";
import type { ChatMessage, SourceReference } from "@/lib/types";

interface ChatState {
  conversationId: string | null;
  messages: ChatMessage[];
  isStreaming: boolean;
  statusNode: string | null;
  error: string | null;

  setConversation: (id: string) => void;
  addUserMessage: (content: string) => void;
  beginAssistantMessage: () => string;
  appendToken: (messageId: string, delta: string) => void;
  setCitations: (messageId: string, sources: SourceReference[]) => void;
  finishMessage: (messageId: string) => void;
  setStatus: (node: string | null) => void;
  setError: (error: string | null) => void;
  setStreaming: (streaming: boolean) => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  conversationId: null,
  messages: [],
  isStreaming: false,
  statusNode: null,
  error: null,

  setConversation: (id) => set({ conversationId: id }),

  addUserMessage: (content) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { id: crypto.randomUUID(), role: "user", content },
      ],
    })),

  beginAssistantMessage: () => {
    const id = crypto.randomUUID();
    set((state) => ({
      messages: [
        ...state.messages,
        { id, role: "assistant", content: "", streaming: true, sources: [] },
      ],
    }));
    return id;
  },

  appendToken: (messageId, delta) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === messageId ? { ...m, content: m.content + delta } : m
      ),
    })),

  setCitations: (messageId, sources) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === messageId
          ? { ...m, sources: [...(m.sources ?? []), ...sources] }
          : m
      ),
    })),

  finishMessage: (messageId) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === messageId ? { ...m, streaming: false } : m
      ),
      isStreaming: false,
      statusNode: null,
    })),

  setStatus: (node) => set({ statusNode: node }),
  setError: (error) => set({ error }),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  reset: () =>
    set({
      conversationId: null,
      messages: [],
      isStreaming: false,
      statusNode: null,
      error: null,
    }),
}));

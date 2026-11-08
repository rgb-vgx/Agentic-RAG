"use client";

/** Chat page — sends queries, consumes the SSE stream, renders sources. */

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { streamChat } from "@/lib/stream";
import { useChatStore } from "@/store/chat";
import type { ModelOption, StreamEvent, SourceReference } from "@/lib/types";

const STATUS_LABELS: Record<string, string> = {
  resolve_context: "Resolving conversation context…",
  analyze: "Analyzing your question…",
  route: "Choosing retrieval path…",
  plan: "Planning retrieval…",
  retrieve: "Searching documents…",
  evaluate: "Checking evidence…",
  synthesize: "Writing the answer…",
  validate_citations: "Validating citations…",
};

/** Turn [R1] markers into link syntax so they can render as badges. */
function citationify(text: string): string {
  return text.replace(/\[(R\d+)\]/g, "[$1](#ref-$1)");
}

export default function ChatPage() {
  const {
    messages,
    isStreaming,
    statusNode,
    error,
    addUserMessage,
    beginAssistantMessage,
    appendToken,
    setCitations,
    finishMessage,
    setStatus,
    setError,
    setStreaming,
  } = useChatStore();

  const [input, setInput] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .createConversation()
      .then((conversation) => {
        if (!cancelled) setConversationId(conversation.id);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [setError]);

  useEffect(() => {
    let cancelled = false;
    api
      .listModels()
      .then(({ items, default: defaultModel }) => {
        if (cancelled) return;
        setModels(items);
        setModel(defaultModel);
      })
      .catch(() => {
        // The catalogue is behind auth; until Bearer wiring lands the
        // selector stays empty and the server applies its default model.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, statusNode]);

  async function send() {
    const query = input.trim();
    if (!query || isStreaming) return;
    if (!conversationId) {
      setError("Conversation is not ready yet");
      return;
    }

    setInput("");
    setError(null);
    setStreaming(true);
    addUserMessage(query);

    const assistantId = beginAssistantMessage();
    const collectedSources: SourceReference[] = [];
    abortRef.current = new AbortController();

    try {
      await streamChat(
        {
          conversation_id: conversationId,
          query,
          model: model || undefined,
        },
        (event: StreamEvent) => {
          switch (event.event) {
            case "status": {
              const node = (event.data as { node?: string }).node;
              if (node) setStatus(node);
              break;
            }
            case "token":
              appendToken(assistantId, (event.data as { delta: string }).delta);
              break;
            case "citation": {
              const citation = event.data as SourceReference;
              collectedSources.push(citation);
              setCitations(assistantId, [citation]);
              break;
            }
            case "completed":
              setStatus(null);
              break;
            case "error":
              setError((event.data as { message: string }).message);
              break;
          }
        },
        abortRef.current.signal
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      finishMessage(assistantId);
      abortRef.current = null;
    }
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }

  return (
    <>
      <header className="chat-header">
        <h1>Document Q&amp;A</h1>
        <div className="chat-controls">
          {isStreaming && statusNode && (
            <span className="status-line">
              {STATUS_LABELS[statusNode] ?? statusNode}
            </span>
          )}
          <select
            className="model-select"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={isStreaming || models.length === 0}
            aria-label="Model"
          >
            {models.length === 0 && (
              <option value="">Default model</option>
            )}
            {models.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label} ({option.provider})
              </option>
            ))}
          </select>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            Ask a question about your uploaded documents.
          </div>
        )}

        {messages.map((message) => (
          <div key={message.id} className={`message ${message.role}`}>
            {message.role === "assistant" ? (
              message.content ? (
                <div className="markdown">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ href, children }) =>
                        href?.startsWith("#ref-") ? (
                          <span className="citation-ref">[{children}]</span>
                        ) : (
                          <a href={href} target="_blank" rel="noreferrer">
                            {children}
                          </a>
                        ),
                    }}
                  >
                    {citationify(message.content)}
                  </ReactMarkdown>
                </div>
              ) : message.streaming && isStreaming ? (
                <span className="status-line">Thinking…</span>
              ) : null
            ) : (
              message.content
            )}

            {message.sources && message.sources.length > 0 && (
              <div className="sources">
                {message.sources.map((source) => (
                  <div
                    key={`${source.reference_id}-${source.chunk_id}`}
                    className="source-item"
                  >
                    <span className="ref">[{source.reference_id}]</span>
                    <span className="heading">
                      {source.heading_path.join(" › ")}
                      {source.page_start ? ` · p.${source.page_start}` : ""}
                    </span>
                    <div className="snippet">{source.snippet}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="composer">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
          disabled={isStreaming}
        />
        <button onClick={() => void send()} disabled={isStreaming || !input.trim()}>
          {isStreaming ? "…" : "Send"}
        </button>
      </div>
    </>
  );
}

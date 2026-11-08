"use client";

/** Chat page — sends queries, consumes the SSE stream, renders sources. */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { streamChat } from "@/lib/stream";
import { useChatStore } from "@/store/chat";
import type { ModelOption, StreamEvent, SourceReference } from "@/lib/types";
import {
  ArrowUpIcon,
  ChevronDownIcon,
  DocsIcon,
  LayersIcon,
  PlusIcon,
  QuoteIcon,
  SearchIcon,
  SlidersIcon,
  SparkIcon,
  SparklesIcon,
  UploadIcon,
} from "@/components/icons";
import Robot from "@/components/Robot";

const STATUS_LABELS: Record<string, string> = {
  resolve_context: "Đang đọc ngữ cảnh hội thoại…",
  analyze: "Đang phân tích câu hỏi…",
  route: "Đang chọn cách truy xuất…",
  plan: "Đang lập kế hoạch tìm kiếm…",
  retrieve: "Đang tìm trong tài liệu…",
  evaluate: "Đang kiểm tra bằng chứng…",
  synthesize: "Đang viết câu trả lời…",
  validate_citations: "Đang kiểm chứng trích dẫn…",
};

const SUGGESTIONS = [
  {
    title: "Hỏi đáp có căn cứ",
    body: "Đặt câu hỏi trên toàn bộ tài liệu đã index, không chỉ một file.",
    action: "Bắt đầu hỏi",
    tone: "amber",
    icon: LayersIcon,
    href: null,
  },
  {
    title: "Truy vết mọi trích dẫn",
    body: "Mở đúng chunk, trang và tiêu đề của nguồn được trích dẫn.",
    action: "Xem tài liệu",
    tone: "sky",
    icon: QuoteIcon,
    href: "/documents",
  },
  {
    title: "Giữ kho tri thức mới",
    body: "Tải PDF, DOCX hoặc TXT và index lại trong vài phút.",
    action: "Tải tài liệu",
    tone: "mint",
    icon: UploadIcon,
    href: "/documents",
  },
];

const PROMPTS = [
  {
    label: "Tìm nguồn",
    icon: SearchIcon,
    text: "Tìm trong tài liệu những đoạn liên quan đến ",
  },
  {
    label: "Tóm tắt",
    icon: SparklesIcon,
    text: "Tóm tắt nội dung chính của tài liệu ",
  },
  {
    label: "So sánh điều khoản",
    icon: DocsIcon,
    text: "So sánh các quy định về ",
  },
];

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
    reset,
  } = useChatStore();

  const [input, setInput] = useState("");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const hasConversation = messages.length > 0;

  function startConversation() {
    api
      .createConversation()
      .then((conversation) => {
        setConversationId(conversation.id);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
      });
  }

  useEffect(() => {
    startConversation();
    // Runs once on mount; startConversation only touches setState.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    if (!hasConversation) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, statusNode, hasConversation]);

  async function send() {
    const query = input.trim();
    if (!query || isStreaming) return;
    if (!conversationId) {
      setError("Cuộc hội thoại chưa sẵn sàng, thử lại sau một giây.");
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
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : String(err));
      }
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

  function usePrompt(text: string) {
    setInput(text);
    const field = inputRef.current;
    if (!field) return;
    field.focus();
    field.setSelectionRange(text.length, text.length);
  }

  function newChat() {
    abortRef.current?.abort();
    abortRef.current = null;
    reset();
    setInput("");
    setError(null);
    setConversationId(null);
    startConversation();
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  const composer = (
    <div className="composer-wrap">
      <div className="composer-meta">
        <span>
          <SparkIcon size={13} /> Trợ lý RAG nội bộ
        </span>
        <span className="composer-stack">
          qwen3-embedding:8b · Qwen3-Reranker-4B
        </span>
      </div>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <div className="composer-row">
          <Link
            href="/documents"
            className="composer-plus"
            aria-label="Thêm tài liệu"
            title="Thêm tài liệu"
          >
            <PlusIcon size={19} />
          </Link>
          <textarea
            id="chat-input"
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Hỏi về tài liệu của bạn…"
            rows={1}
            disabled={isStreaming}
            aria-label="Câu hỏi"
          />
          <button
            type="submit"
            className="send-button"
            disabled={isStreaming || !input.trim()}
            aria-label="Gửi câu hỏi"
          >
            <ArrowUpIcon size={19} />
          </button>
        </div>

        <div className="composer-actions">
          {PROMPTS.map(({ label, icon: Icon, text }) => (
            <button
              key={label}
              type="button"
              className="prompt-chip"
              onClick={() => usePrompt(text)}
              disabled={isStreaming}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
          <button
            type="button"
            className="prompt-chip prompt-chip-icon"
            onClick={() => inputRef.current?.focus()}
            aria-label="Thêm hành động"
            title="Thêm hành động"
          >
            <SlidersIcon size={15} />
          </button>
        </div>
      </form>
    </div>
  );

  return (
    <div className="chat-page">
      <header className="topbar">
        <Link href="/chat" className="assistant-pill">
          <span className="assistant-glyph">
            <SparkIcon size={15} />
          </span>
          Agentic RAG
          <span className="assistant-version">v1.0</span>
          <ChevronDownIcon size={13} />
        </Link>

        <div className="topbar-title">Document Q&amp;A</div>

        <div className="topbar-actions">
          <label className="model-pill">
            <span className="sr-only">Model</span>
            <select
              value={model}
              onChange={(event) => setModel(event.target.value)}
              disabled={isStreaming || models.length === 0}
              aria-label="Model"
            >
              {models.length === 0 && <option value="">Model mặc định</option>}
              {models.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
            <ChevronDownIcon size={12} />
          </label>
          <button type="button" className="primary-pill" onClick={newChat}>
            <SparklesIcon size={15} />
            <span className="primary-label">Cuộc trò chuyện mới</span>
          </button>
        </div>
      </header>

      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}

      {hasConversation ? (
        <div className="messages">
          {messages.map((message) => (
            <div key={message.id} className={`message ${message.role}`}>
              {message.role === "assistant" ? (
                <>
                  <div className="assistant-meta">
                    <span className="assistant-glyph small">
                      <SparkIcon size={12} />
                    </span>
                    Agentic RAG
                  </div>
                  {message.content ? (
                    <div className="markdown">
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          a: ({ href, children }) =>
                            href?.startsWith("#ref-") ? (
                              <span className="citation-ref">{children}</span>
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
                    <span className="status-line">
                      <span className="status-dot" />
                      {statusNode
                        ? (STATUS_LABELS[statusNode] ?? statusNode)
                        : "Đang suy nghĩ…"}
                    </span>
                  ) : null}

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
                            {source.page_start ? ` · tr.${source.page_start}` : ""}
                          </span>
                          <div className="snippet">{source.snippet}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                message.content
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      ) : (
        <div className="chat-empty">
          <section className="hero">
            <div className="hero-copy">
              <h1>
                Chào bạn, sẵn sàng
                <br />
                tra cứu tài liệu?
              </h1>
              <p>
                Đặt câu hỏi bằng tiếng Việt. Câu trả lời luôn kèm trích dẫn tới
                đúng điều khoản, trang và đoạn gốc.
              </p>
            </div>
            <Robot />
          </section>

          <div className="suggestion-grid">
            {SUGGESTIONS.map(({ title, body, action, tone, icon: Icon, href }) => {
              const content = (
                <>
                  <span className={`suggestion-icon ${tone}`}>
                    <Icon size={22} />
                  </span>
                  <span className="suggestion-title">{title}</span>
                  <span className="suggestion-body">{body}</span>
                  <span className="suggestion-action">{action}</span>
                </>
              );
              return href ? (
                <Link key={title} href={href} className="suggestion-card">
                  {content}
                </Link>
              ) : (
                <button
                  key={title}
                  type="button"
                  className="suggestion-card"
                  onClick={() => inputRef.current?.focus()}
                >
                  {content}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {composer}
    </div>
  );
}

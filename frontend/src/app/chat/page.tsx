"use client";

/** Chat page — sends queries, consumes the SSE stream, renders sources. */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import { streamChat } from "@/lib/stream";
import { useChatStore } from "@/store/chat";
import type {
  ChatMessage,
  ConversationItem,
  ModelOption,
  StreamEvent,
  SourceReference,
} from "@/lib/types";
import {
  ArrowUpIcon,
  ChevronDownIcon,
  DocsIcon,
  HistoryIcon,
  LayersIcon,
  PlusIcon,
  QuoteIcon,
  SearchIcon,
  SlidersIcon,
  SparkIcon,
  SparklesIcon,
  UploadIcon,
} from "@/components/icons";
import HistoryPanel from "@/components/HistoryPanel";
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
  working: "Đang xử lý…",
};

function describeStatus(data: {
  node?: string;
  update?: Record<string, unknown>;
}): string {
  const update = data.update ?? {};
  const parts: string[] = [];
  const lastNode = typeof update.last_node === "string" ? update.last_node : "";
  if (lastNode && lastNode !== data.node) {
    parts.push(STATUS_LABELS[lastNode] ?? lastNode);
  }
  const elapsed = update.elapsed_seconds;
  if (typeof elapsed === "number") parts.push(`${elapsed}s`);
  const plan = update.retrieval_plan as
    | { retrieval_tasks?: unknown[] }
    | undefined;
  if (Array.isArray(plan?.retrieval_tasks)) {
    parts.push(`${plan.retrieval_tasks.length} truy vấn`);
  }
  const groups = update.evidence_groups as
    | Array<{ count?: number }>
    | undefined;
  if (Array.isArray(groups)) {
    const count = groups.reduce(
      (total, group) => total + (group.count ?? 0),
      0
    );
    parts.push(`${count} bằng chứng`);
  }
  return parts.join(" · ");
}

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

/** Newest conversations first, falling back to creation time. */
function sortConversations(items: ConversationItem[]): ConversationItem[] {
  return [...items].sort((a, b) => {
    const aTime = new Date(a.updated_at ?? a.created_at ?? 0).getTime();
    const bTime = new Date(b.updated_at ?? b.created_at ?? 0).getTime();
    return (Number.isNaN(bTime) ? 0 : bTime) - (Number.isNaN(aTime) ? 0 : aTime);
  });
}

/** Stored sources are untyped JSON; keep only entries safe to render. */
function normalizeSources(raw: unknown[]): SourceReference[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const source = entry as Partial<SourceReference>;
    if (
      typeof source.reference_id !== "string" ||
      typeof source.chunk_id !== "string"
    ) {
      return [];
    }
    return [
      {
        reference_id: source.reference_id,
        chunk_id: source.chunk_id,
        document_id:
          typeof source.document_id === "string" ? source.document_id : "",
        page_start:
          typeof source.page_start === "number" ? source.page_start : null,
        page_end: typeof source.page_end === "number" ? source.page_end : null,
        heading_path: Array.isArray(source.heading_path)
          ? source.heading_path.filter(
              (part): part is string => typeof part === "string"
            )
          : [],
        snippet: typeof source.snippet === "string" ? source.snippet : "",
        url: typeof source.url === "string" ? source.url : null,
      },
    ];
  });
}

export default function ChatPage() {
  const {
    messages,
    isStreaming,
    statusNode,
    error,
    setMessages,
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
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [autoTitle, setAutoTitle] = useState(true);
  const [titleModel, setTitleModel] = useState("");
  const [statusDetail, setStatusDetail] = useState("");
  const [webSearch, setWebSearch] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const conversationPromiseRef = useRef<Promise<string> | null>(null);

  const hasConversation = messages.length > 0;

  async function refreshConversations() {
    try {
      const { items } = await api.listConversations();
      setConversations(sortConversations(items));
    } catch {
      // History is a convenience layer; a listing failure must not block chat.
    }
  }

  function updateAutoTitle(enabled: boolean) {
    setAutoTitle(enabled);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("rag.autoTitle", String(enabled));
    }
  }

  function updateTitleModel(nextModel: string) {
    setTitleModel(nextModel);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("rag.titleModel", nextModel);
    }
  }

  async function maybeAutoTitle(id: string) {
    if (!autoTitle) return;
    try {
      await api.autoTitleConversation(id, titleModel || model || undefined);
      await refreshConversations();
    } catch {
      // Keep the first-message fallback; title generation is best-effort.
    }
  }

  function ensureConversation(): Promise<string> {
    if (conversationId) return Promise.resolve(conversationId);
    if (!conversationPromiseRef.current) {
      conversationPromiseRef.current = api
        .createConversation()
        .then((conversation) => {
          setConversationId(conversation.id);
          void refreshConversations();
          return conversation.id;
        })
        .catch((err) => {
          conversationPromiseRef.current = null;
          throw err;
        });
    }
    return conversationPromiseRef.current;
  }

  function startConversation() {
    void ensureConversation().catch((err) => {
      setError(err instanceof Error ? err.message : String(err));
    });
  }

  async function openConversation(id: string) {
    if (isStreaming) {
      abortRef.current?.abort();
      abortRef.current = null;
      setStreaming(false);
      setStatus(null);
    }
    setHistoryLoading(true);
    setError(null);
    try {
      const items = await api.listMessages(id);
      const loaded: ChatMessage[] = items.map((item) => ({
        id: item.id,
        role: item.role,
        content: item.content,
        sources: normalizeSources(item.sources),
        model_name: item.model_name ?? null,
      }));
      setMessages(loaded);
      setConversationId(id);
      if (typeof window !== "undefined" && window.innerWidth <= 760) {
        setHistoryOpen(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setHistoryLoading(false);
    }
  }

  async function renameConversation(id: string, title: string) {
    try {
      await api.renameConversation(id, title);
      await refreshConversations();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function removeConversation(id: string) {
    try {
      await api.deleteConversation(id);
      if (id === conversationId) {
        newChat();
      } else {
        await refreshConversations();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => {
    startConversation();
    void refreshConversations();
    if (typeof window !== "undefined") {
      const storedEnabled = window.localStorage.getItem("rag.autoTitle");
      if (storedEnabled === "false") setAutoTitle(false);
      const storedModel = window.localStorage.getItem("rag.titleModel");
      if (storedModel) setTitleModel(storedModel);
    }
    // Runs once on mount; the helpers only touch state and the API client.
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
        setTitleModel((current) => current || defaultModel);
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
    let activeConversationId = conversationId;
    if (!activeConversationId) {
      try {
        activeConversationId = await ensureConversation();
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Không tạo được cuộc hội thoại, thử lại sau."
        );
        return;
      }
    }

    setInput("");
    setError(null);
    setStatusDetail("");
    setStreaming(true);
    addUserMessage(query);

    const assistantId = beginAssistantMessage();
    const collectedSources: SourceReference[] = [];
    abortRef.current = new AbortController();

    try {
      await streamChat(
        {
          conversation_id: activeConversationId,
          query,
          model: model || undefined,
          web_search: webSearch,
        },
        (event: StreamEvent) => {
          switch (event.event) {
            case "status": {
              const payload = event.data as {
                node?: string;
                update?: Record<string, unknown>;
              };
              if (payload.node) setStatus(payload.node);
              setStatusDetail(describeStatus(payload));
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
      void refreshConversations();
      void maybeAutoTitle(activeConversationId);
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
    conversationPromiseRef.current = null;
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
      <HistoryPanel
        conversations={conversations}
        activeId={conversationId}
        loading={historyLoading}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onSelect={(id) => void openConversation(id)}
        onCreate={newChat}
        onRename={(id, title) => void renameConversation(id, title)}
        onDelete={(id) => void removeConversation(id)}
        autoTitle={autoTitle}
        onAutoTitleChange={updateAutoTitle}
        titleModel={titleModel}
        onTitleModelChange={updateTitleModel}
        models={models}
      />

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
          <button
            type="button"
            className={`web-toggle${webSearch ? " active" : ""}`}
            onClick={() => setWebSearch((value) => !value)}
            aria-pressed={webSearch}
            aria-label="Bật/tắt tìm kiếm web"
            title={webSearch ? "Đang bật tìm kiếm web" : "Bật tìm kiếm web"}
          >
            <SearchIcon size={15} />
            Web
          </button>
          <button
            type="button"
            className="history-toggle"
            onClick={() => setHistoryOpen((open) => !open)}
            aria-label="Lịch sử hội thoại"
            aria-expanded={historyOpen}
            title="Lịch sử hội thoại"
          >
            <HistoryIcon size={17} />
          </button>
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
                      {statusDetail && (
                        <span className="status-detail">{statusDetail}</span>
                      )}
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
                            {source.url ? (
                              <a
                                href={source.url}
                                target="_blank"
                                rel="noreferrer noopener"
                              >
                                {source.heading_path.join(" › ")}
                              </a>
                            ) : (
                              source.heading_path.join(" › ")
                            )}
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

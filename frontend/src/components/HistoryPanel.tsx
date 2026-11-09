"use client";

/**
 * Conversation history drawer.
 *
 * The chat page owns the data: it refreshes the list with
 * `api.listConversations()` and loads a selected conversation with
 * `api.listMessages(id)`. This component only renders the rows and forwards
 * create/select/rename/delete intents.
 */

import { useState } from "react";
import type { ConversationItem } from "@/lib/types";
import { PlusIcon } from "@/components/icons";

interface HistoryPanelProps {
  conversations: ConversationItem[];
  activeId: string | null;
  loading: boolean;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

function conversationTitle(conversation: ConversationItem): string {
  return conversation.title?.trim() || "New conversation";
}

function formatWhen(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "Vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} ngày trước`;
  return date.toLocaleDateString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

export default function HistoryPanel({
  conversations,
  activeId,
  loading,
  open,
  onClose,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: HistoryPanelProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  if (!open) return null;

  function beginRename(
    event: React.MouseEvent<HTMLButtonElement>,
    conversation: ConversationItem
  ) {
    event.stopPropagation();
    setRenamingId(conversation.id);
    setDraftTitle(conversationTitle(conversation));
  }

  function cancelRename(event?: React.MouseEvent | React.FormEvent) {
    event?.stopPropagation();
    setRenamingId(null);
    setDraftTitle("");
  }

  function commitRename(event: React.FormEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (!renamingId) return;
    const title = draftTitle.trim() || "New conversation";
    onRename(renamingId, title);
    cancelRename();
  }

  function handleDelete(
    event: React.MouseEvent<HTMLButtonElement>,
    conversation: ConversationItem
  ) {
    event.stopPropagation();
    if (window.confirm(`Xoá "${conversationTitle(conversation)}"?`)) {
      onDelete(conversation.id);
    }
  }

  return (
    <>
      <div className="history-scrim" onClick={onClose} aria-hidden="true" />
      <aside className="history-panel" aria-label="Lịch sử hội thoại">
        <div className="history-head">
          <div>
            <span className="history-eyebrow">Trợ lý RAG nội bộ</span>
            <h2>Lịch sử hội thoại</h2>
          </div>
          <button
            type="button"
            className="history-close"
            onClick={onClose}
            aria-label="Đóng lịch sử"
            title="Đóng"
          >
            ×
          </button>
        </div>

        <button type="button" className="history-new" onClick={onCreate}>
          <PlusIcon size={16} />
          Cuộc trò chuyện mới
        </button>

        <div className="history-list">
          {loading ? (
            <p className="history-empty">Đang tải lịch sử…</p>
          ) : conversations.length === 0 ? (
            <p className="history-empty">
              Chưa có cuộc trò chuyện nào. Hãy bắt đầu một cuộc trò chuyện mới.
            </p>
          ) : (
            conversations.map((conversation) => {
              const active = conversation.id === activeId;
              return (
                <div
                  key={conversation.id}
                  role="button"
                  tabIndex={0}
                  className={`history-item${active ? " active" : ""}`}
                  onClick={() => onSelect(conversation.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelect(conversation.id);
                    }
                  }}
                >
                  {renamingId === conversation.id ? (
                    <form
                      className="history-rename"
                      onSubmit={commitRename}
                      onClick={(event) => event.stopPropagation()}
                    >
                      <input
                        value={draftTitle}
                        onChange={(event) => setDraftTitle(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") {
                            event.preventDefault();
                            cancelRename();
                          }
                        }}
                        aria-label="Tên cuộc trò chuyện"
                        autoFocus
                      />
                      <button type="submit" aria-label="Lưu tên">
                        Lưu
                      </button>
                      <button
                        type="button"
                        onClick={(event) => cancelRename(event)}
                        aria-label="Huỷ đổi tên"
                      >
                        Huỷ
                      </button>
                    </form>
                  ) : (
                    <>
                      <div className="history-item-main">
                        <span className="history-item-title">
                          {conversationTitle(conversation)}
                        </span>
                        <span className="history-item-time">
                          {formatWhen(
                            conversation.updated_at ?? conversation.created_at
                          )}
                        </span>
                      </div>
                      <div className="history-item-actions">
                        <button
                          type="button"
                          className="history-action"
                          onClick={(event) => beginRename(event, conversation)}
                          aria-label={`Đổi tên ${conversationTitle(conversation)}`}
                          title="Đổi tên"
                        >
                          ✎
                        </button>
                        <button
                          type="button"
                          className="history-action danger"
                          onClick={(event) => handleDelete(event, conversation)}
                          aria-label={`Xoá ${conversationTitle(conversation)}`}
                          title="Xoá"
                        >
                          🗑
                        </button>
                      </div>
                    </>
                  )}
                </div>
              );
            })
          )}
        </div>
      </aside>
    </>
  );
}

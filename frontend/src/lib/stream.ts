/**
 * SSE consumption via fetch + ReadableStream.
 *
 * EventSource can't POST, so the chat stream uses fetch with a streamed
 * body and parses the SSE wire format manually. Frames arrive as:
 *
 *   event: token
 *   data: {"delta":"..."}
 *
 * Each parsed frame is handed to `onEvent`.
 */

import { authHeaders } from "./auth";
import { API_PREFIX } from "./config";
import type { StreamEvent } from "./types";

const PREFIX = API_PREFIX;

export interface ChatStreamRequest {
  conversation_id: string;
  query: string;
  knowledge_base_id?: string;
  use_agentic?: boolean | null;
  model?: string | null;
  web_search?: boolean;
  document_ids?: string[];
}

export async function streamChat(
  request: ChatStreamRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(`${PREFIX}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(request),
    signal,
  });

  if (response.status === 401 && typeof window !== "undefined") {
    const { clearAccessToken } = await import("./oauth");
    clearAccessToken();
    window.location.assign("/login");
  }
  if (!response.ok || !response.body) {
    throw new Error(`Chat request failed: ${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line. sse-starlette emits CRLF
      // (and the spec allows both), so split on either line ending.
      const frameSeparator = /\r?\n\r?\n/;
      let separator: RegExpExecArray | null;
      while ((separator = frameSeparator.exec(buffer)) !== null) {
        const rawFrame = buffer.slice(0, separator.index);
        buffer = buffer.slice(separator.index + separator[0].length);
        const parsed = parseFrame(rawFrame);
        if (parsed) onEvent(parsed);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(raw: string): StreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;

  const data = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(data) } as StreamEvent;
  } catch {
    return { event, data } as unknown as StreamEvent;
  }
}

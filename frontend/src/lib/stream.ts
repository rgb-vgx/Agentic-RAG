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

import type { StreamEvent } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const PREFIX = `${API_URL}/api/v1`;

export interface ChatStreamRequest {
  conversation_id: string;
  query: string;
  knowledge_base_id?: string;
  use_agentic?: boolean | null;
}

export async function streamChat(
  request: ChatStreamRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(`${PREFIX}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });

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

      // SSE frames are separated by a blank line
      let separatorIndex: number;
      while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
        const rawFrame = buffer.slice(0, separatorIndex);
        buffer = buffer.slice(separatorIndex + 2);
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

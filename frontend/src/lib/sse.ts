/**
 * Summary: A small, testable fetch-based client for the investigation SSE stream
 * (plan §5.4, §16 Phase 11 `lib/sse.ts`). It subscribes to the named server-sent
 * events the backend emits (`run.started` … `sar.token` … `run.completed`), sends the
 * same Authorization/demo headers as REST, parses each frame's JSON `data`, and
 * surfaces the stream `id` as `lastEventId`.
 *
 * Key classes:
 * - SseMessage: one parsed server-sent event (type + parsed data + lastEventId).
 * - SseClientOptions: inputs to createSseClient (url, event names, callbacks, fetch).
 * - SseHandle: the returned controller (close the stream).
 *
 * Key functions:
 * - createSseClient: open an EventSource, dispatch parsed named events, return a handle.
 *
 * Notes:
 * - Native EventSource cannot send an Authorization header; fetch + ReadableStream keeps
 *   bearer tokens out of URLs. A non-JSON frame degrades to `data: null` rather than throwing.
 */
import { withSessionHeaders } from "./session";

export interface SseMessage {
  type: string;
  data: unknown;
  lastEventId: string;
}

export interface SseClientOptions {
  url: string;
  events: readonly string[];
  onMessage: (message: SseMessage) => void;
  onOpen?: () => void;
  onError?: (event: Event) => void;
  fetchImpl?: typeof fetch;
}

export interface SseHandle {
  close: () => void;
}

function parseData(raw: unknown): unknown {
  if (typeof raw !== "string") {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function parseFrame(frame: string): { type: string; data: string; lastEventId: string } | null {
  let type = "message";
  let lastEventId = "";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      type = line.slice("event:".length).trimStart();
    } else if (line.startsWith("data:")) {
      data.push(line.slice("data:".length).trimStart());
    } else if (line.startsWith("id:")) {
      lastEventId = line.slice("id:".length).trimStart();
    }
  }
  if (data.length === 0) {
    return null;
  }
  return { type, data: data.join("\n"), lastEventId };
}

export function createSseClient(options: SseClientOptions): SseHandle {
  const fetchImpl = options.fetchImpl ?? fetch;
  const controller = new AbortController();
  const subscribed = new Set(options.events);
  let closed = false;

  async function pump(): Promise<void> {
    try {
      const response = await fetchImpl(
        options.url,
        withSessionHeaders({ headers: { Accept: "text/event-stream" }, signal: controller.signal }),
      );
      if (!response.ok || !response.body) {
        throw new Error("SSE stream failed.");
      }
      options.onOpen?.();
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!closed) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const parsed = parseFrame(frame);
          if (parsed && subscribed.has(parsed.type)) {
            options.onMessage({
              type: parsed.type,
              data: parseData(parsed.data),
              lastEventId: parsed.lastEventId,
            });
          }
        }
      }
    } catch {
      if (!closed) {
        options.onError?.(new Event("error"));
      }
    }
  }

  void pump();
  return {
    close: () => {
      closed = true;
      controller.abort();
    },
  };
}

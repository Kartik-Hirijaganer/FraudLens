/**
 * Summary: A small, testable wrapper over the browser `EventSource` for the
 * investigation stream (plan §5.4, §16 Phase 11 `lib/sse.ts`). It subscribes to the
 * named server-sent events the backend emits (`run.started` … `sar.token` …
 * `run.completed`), parses each frame's JSON `data`, and surfaces the native
 * `lastEventId` so a reconnect resumes exactly from the last persisted `seq` (the
 * backend replays `analysis_run_events` from `Last-Event-ID`). The `EventSource`
 * factory is injectable so tests drive a fake without a real network/EventSource
 * (jsdom has none); the page passes the real one.
 *
 * Key classes:
 * - SseMessage: one parsed server-sent event (type + parsed data + lastEventId).
 * - SseClientOptions: inputs to createSseClient (url, event names, callbacks, factory).
 * - SseHandle: the returned controller (close the stream).
 *
 * Key functions:
 * - createSseClient: open an EventSource, dispatch parsed named events, return a handle.
 *
 * Notes:
 * - `EventSource` can't send an Authorization header; in local-demo the gateway dev
 *   bypass needs none. A non-JSON frame degrades to `data: null` rather than throwing.
 */
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
  eventSourceFactory?: (url: string) => EventSource;
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

export function createSseClient(options: SseClientOptions): SseHandle {
  const factory = options.eventSourceFactory ?? ((url: string) => new EventSource(url));
  const source = factory(options.url);
  for (const type of options.events) {
    source.addEventListener(type, (event: MessageEvent) => {
      options.onMessage({ type, data: parseData(event.data), lastEventId: event.lastEventId });
    });
  }
  source.onopen = (): void => options.onOpen?.();
  source.onerror = (event: Event): void => options.onError?.(event);
  return { close: () => source.close() };
}

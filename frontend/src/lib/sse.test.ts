import { describe, expect, it, vi } from "vitest";

import { createSseClient, type SseMessage } from "./sse";

class FakeEventSource {
  listeners = new Map<string, (event: MessageEvent) => void>();
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  constructor(readonly url: string) {}
  addEventListener(type: string, cb: (event: MessageEvent) => void): void {
    this.listeners.set(type, cb);
  }
  close(): void {
    this.closed = true;
  }
  emit(type: string, data: string, lastEventId = ""): void {
    this.listeners.get(type)?.({ data, lastEventId } as MessageEvent);
  }
}

function setup(events: string[]) {
  let source!: FakeEventSource;
  const messages: SseMessage[] = [];
  const onOpen = vi.fn();
  const onError = vi.fn();
  const handle = createSseClient({
    url: "/stream",
    events,
    onMessage: (message) => messages.push(message),
    onOpen,
    onError,
    eventSourceFactory: (url) => {
      source = new FakeEventSource(url);
      return source as unknown as EventSource;
    },
  });
  return { source, messages, onOpen, onError, handle };
}

describe("createSseClient", () => {
  it("parses subscribed events and exposes lastEventId", () => {
    const { source, messages } = setup(["run.started", "sar.token"]);
    source.emit("run.started", JSON.stringify({ transactionId: "t1" }), "1");
    expect(messages[0]).toEqual({
      type: "run.started",
      data: { transactionId: "t1" },
      lastEventId: "1",
    });
  });

  it("degrades a non-JSON frame to null data", () => {
    const { source, messages } = setup(["sar.token"]);
    source.emit("sar.token", "<<not json>>");
    expect(messages[0].data).toBeNull();
  });

  it("wires onOpen / onError and closes the source", () => {
    const { source, onOpen, onError, handle } = setup(["run.started"]);
    source.onopen?.(new Event("open"));
    source.onerror?.(new Event("error"));
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onError).toHaveBeenCalledOnce();
    handle.close();
    expect(source.closed).toBe(true);
  });

  it("falls back to the global EventSource when no factory is given", () => {
    const close = vi.fn();
    class GlobalFake {
      addEventListener(): void {}
      close = close;
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
    }
    (globalThis as { EventSource?: unknown }).EventSource = vi.fn(() => new GlobalFake());
    try {
      const handle = createSseClient({
        url: "/stream",
        events: ["run.started"],
        onMessage: () => {},
      });
      handle.close();
      expect(close).toHaveBeenCalledOnce();
    } finally {
      delete (globalThis as { EventSource?: unknown }).EventSource;
    }
  });
});

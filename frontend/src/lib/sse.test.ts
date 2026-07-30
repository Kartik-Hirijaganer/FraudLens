import { afterEach, describe, expect, it, vi } from "vitest";

import { createSseClient, type SseMessage } from "./sse";
import { signIn, signOut } from "./session";
import { demoPersona } from "../test/factories";

function streamResponse(...frames: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) {
          controller.enqueue(encoder.encode(frame));
        }
        controller.close();
      },
    }),
    { status: 200 },
  );
}

async function waitForMessages(messages: SseMessage[], count = 1): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    if (messages.length >= count) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

afterEach(() => {
  signOut();
});

describe("createSseClient", () => {
  it("parses subscribed events and exposes lastEventId", async () => {
    const messages: SseMessage[] = [];
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        streamResponse('id: 1\nevent: run.started\ndata: {"transactionId":"t1"}\n\n'),
      ),
    );
    createSseClient({
      url: "/stream",
      events: ["run.started", "sar.token"],
      onMessage: (message) => messages.push(message),
      fetchImpl: fetchMock,
    });

    await waitForMessages(messages);

    expect(messages[0]).toEqual({
      type: "run.started",
      data: { transactionId: "t1" },
      lastEventId: "1",
    });
  });

  it("degrades a non-JSON frame to null data", async () => {
    const messages: SseMessage[] = [];
    createSseClient({
      url: "/stream",
      events: ["sar.token"],
      onMessage: (message) => messages.push(message),
      fetchImpl: vi.fn(() =>
        Promise.resolve(streamResponse("event: sar.token\ndata: <<not json>>\n\n")),
      ),
    });

    await waitForMessages(messages);

    expect(messages[0].data).toBeNull();
  });

  it("wires onOpen, onError, and close", async () => {
    const onOpen = vi.fn();
    const onError = vi.fn();
    const handle = createSseClient({
      url: "/stream",
      events: ["run.started"],
      onMessage: () => undefined,
      onOpen,
      onError,
      fetchImpl: vi.fn(() => Promise.resolve(new Response(null, { status: 500 }))),
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onOpen).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledOnce();
    handle.close();
  });

  it("sends bearer auth headers without putting tokens in the URL", async () => {
    signIn("reviewer@example.test", false, "reviewer", "token-1");
    const fetchMock = vi.fn(() =>
      Promise.resolve(streamResponse('event: run.started\ndata: {"ok":true}\n\n')),
    );
    const messages: SseMessage[] = [];
    createSseClient({
      url: "/api/v1/investigations/r1/stream",
      events: ["run.started"],
      onMessage: (message) => messages.push(message),
      fetchImpl: fetchMock,
    });

    await waitForMessages(messages);

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/investigations/r1/stream");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer token-1");
    expect(url).not.toContain("token-1");
  });

  it("sends the demo role header for dev-bypass sessions", async () => {
    const persona = demoPersona("analyst");
    signIn(persona.email, false, persona.role);
    const fetchMock = vi.fn(() =>
      Promise.resolve(streamResponse('event: run.started\ndata: {"ok":true}\n\n')),
    );
    createSseClient({
      url: "/stream",
      events: ["run.started"],
      onMessage: () => undefined,
      fetchImpl: fetchMock,
    });

    await new Promise((resolve) => setTimeout(resolve, 0));

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>)["X-FraudLens-Demo-Role"]).toBe("analyst");
  });
});

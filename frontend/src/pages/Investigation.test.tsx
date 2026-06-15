import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SseClientOptions, SseHandle } from "../lib/sse";
import { makeClient, snapshot } from "../test/factories";
import { Investigation } from "./Investigation";

afterEach(() => {
  window.location.hash = "";
});

function streamHarness() {
  let options: SseClientOptions | undefined;
  const close = vi.fn();
  const factory = (received: SseClientOptions): SseHandle => {
    options = received;
    return { close };
  };
  const emit = (type: string, data: unknown, lastEventId = ""): void => {
    act(() => options?.onMessage({ type, data, lastEventId }));
  };
  const fail = (): void => {
    act(() => options?.onError?.(new Event("error")));
  };
  return { factory, emit, fail, close };
}

describe("Investigation", () => {
  it("streams the pipeline from cold start to completion", async () => {
    const harness = streamHarness();
    render(<Investigation runId="run-1" client={makeClient()} createStream={harness.factory} />);
    expect(screen.getByText(/Waking the service/)).toBeInTheDocument();

    harness.emit("run.started", { transactionId: "tx-1" }, "1");
    expect(screen.queryByText(/Waking the service/)).not.toBeInTheDocument();

    harness.emit("step.scoring.completed", { fraudProbability: 0.9, modelVersion: "m1" }, "3");
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "90");

    harness.emit("sar.started", {}, "6");
    harness.emit("sar.token", { token: "Hello" });
    expect(screen.getByText(/Hello/)).toBeInTheDocument();

    harness.emit("run.completed", { riskScore: 0.8, riskBand: "critical", sarDraftId: "s1" }, "7");
    expect(harness.close).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "View alerts" }));
    expect(window.location.hash).toBe("#/alerts");
  });

  it("ignores a failed snapshot reconciliation", async () => {
    const harness = streamHarness();
    const getInvestigation = vi.fn(() => Promise.reject(new Error("offline")));
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ getInvestigation })}
        createStream={harness.factory}
      />,
    );
    await act(async () => {
      harness.fail();
      await Promise.resolve();
    });
    expect(getInvestigation).toHaveBeenCalledWith("run-1");
  });

  it("reconciles from the snapshot on a connection error", async () => {
    const harness = streamHarness();
    const getInvestigation = vi.fn(() =>
      Promise.resolve(
        snapshot({ status: "running", riskScore: null, riskBand: null, sarDraftId: null }),
      ),
    );
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ getInvestigation })}
        createStream={harness.factory}
      />,
    );
    await act(async () => {
      harness.fail();
      await Promise.resolve();
    });
    expect(getInvestigation).toHaveBeenCalledWith("run-1");
    expect(screen.getByText(/Live updates were interrupted/)).toBeInTheDocument();
  });
});

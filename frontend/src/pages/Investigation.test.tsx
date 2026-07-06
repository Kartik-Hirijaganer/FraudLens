import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SarDraftView } from "../lib/api";
import { DEMO_ROLES, signIn, signOut, type UserRole } from "../lib/session";
import type { SseClientOptions, SseHandle } from "../lib/sse";
import { makeClient, sarDraft, snapshot } from "../test/factories";
import { Investigation } from "./Investigation";

function signInAs(role: UserRole): void {
  const demoRole = DEMO_ROLES.find((candidate) => candidate.role === role);
  if (!demoRole) {
    throw new Error(`Missing demo role: ${role}`);
  }
  signIn(demoRole.email, false, demoRole.role);
}

beforeEach(() => {
  signInAs("analyst");
});

afterEach(() => {
  window.location.hash = "";
  signOut();
  vi.clearAllMocks();
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

function runToCompletion(harness: ReturnType<typeof streamHarness>): void {
  harness.emit("run.started", { transactionId: "tx-1" }, "1");
  harness.emit("step.rules.completed", {
    ruleHits: [
      { code: "STRUCTURING", ruleType: "structuring", severity: "high", reason: "near threshold" },
    ],
  });
  harness.emit("step.scoring.completed", { fraudProbability: 0.9, modelVersion: "m1" }, "3");
}

describe("Investigation", () => {
  it("streams the auto-run and walks the wizard from risk to submit", async () => {
    const harness = streamHarness();
    render(<Investigation runId="run-1" client={makeClient()} createStream={harness.factory} />);
    expect(screen.getByText("Build the case")).toBeInTheDocument();
    expect(screen.getByText(/Waking the service/)).toBeInTheDocument();

    harness.emit("run.started", { transactionId: "tx-1" }, "1");
    expect(screen.queryByText(/Waking the service/)).not.toBeInTheDocument();

    harness.emit("step.scoring.completed", { fraudProbability: 0.9, modelVersion: "m1" }, "3");
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "90");
    // Drivers evidence hasn't streamed yet, so advancing is blocked.
    expect(screen.getByRole("button", { name: /continue to drivers/i })).toBeDisabled();

    harness.emit("step.shap.completed", {
      topFeatures: [{ feature: "amount_zscore", value: 4.1, shapValue: 0.5 }],
    });
    expect(screen.getByRole("button", { name: /continue to drivers/i })).toBeEnabled();

    harness.emit("step.rag.completed", {
      mode: "vector",
      citations: [
        { citation: "31 CFR 1020.320", title: "SAR filing", source: "FinCEN", snippet: "…" },
      ],
    });
    harness.emit("sar.started", {}, "6");
    harness.emit("sar.token", { token: "On 22 June 2026, account holder initiated a wire." });
    harness.emit("run.completed", { riskScore: 0.87, riskBand: "high", sarDraftId: "s1" }, "7");
    expect(harness.close).toHaveBeenCalled();

    // Evidence chips are derived from the streamed state (top driver carries its z-score value).
    expect(screen.getByText("Risk: High · 0.87")).toBeInTheDocument();
    expect(screen.getByText("Top driver: Amount Zscore 4.1σ")).toBeInTheDocument();
    expect(screen.getByText("Auto-run complete")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    expect(screen.getByText("amount_zscore")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
    expect(screen.getByText("SAR filing")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));
    expect(screen.getByText(/On 22 June 2026/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /continue to submit/i }));
    expect(screen.getByText("Ready to file")).toBeInTheDocument();
    expect(screen.getByText("Step 5 of 5")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Submit the report" }));
    expect(window.location.hash).toBe("#/alerts");
  });

  it("animates then swaps in the regenerated SAR draft", async () => {
    const harness = streamHarness();
    let resolveRegen: (draft: SarDraftView) => void = () => undefined;
    const regenerateSar = vi.fn(
      () =>
        new Promise<SarDraftView>((resolve) => {
          resolveRegen = resolve;
        }),
    );
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ regenerateSar })}
        createStream={harness.factory}
      />,
    );
    runToCompletion(harness);
    harness.emit("step.shap.completed", {
      topFeatures: [{ feature: "amount", value: 1, shapValue: 0.5 }],
    });
    harness.emit("step.rag.completed", {
      mode: "vector",
      citations: [{ citation: "c", title: "t", source: "FinCEN", snippet: "s" }],
    });
    harness.emit("sar.started", {}, "6");
    harness.emit("sar.token", { token: "**Subject:** wire transfer under review" });
    harness.emit("run.completed", { riskScore: 0.87, riskBand: "high", sarDraftId: "s1" }, "7");

    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));

    // The markdown draft renders formatted, not as literal asterisks.
    expect(screen.getByText("Subject:").tagName).toBe("STRONG");

    await userEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    // While the request is in flight: button loading + disabled, draft busy, submit blocked.
    expect(regenerateSar).toHaveBeenCalledWith("run-1");
    expect(screen.getByRole("button", { name: /Regenerating/i })).toBeDisabled();
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: /continue to submit/i })).toBeDisabled();

    // Resolving the request swaps in the new narrative and clears the loading state.
    await act(async () => {
      resolveRegen(sarDraft({ content: "A freshly regenerated narrative.", version: 2 }));
      await Promise.resolve();
    });
    expect(screen.getByText(/A freshly regenerated narrative\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Regenerate" })).toBeEnabled();
  });

  it("keeps the current draft and re-enables Regenerate when regeneration fails", async () => {
    const harness = streamHarness();
    const regenerateSar = vi.fn(() => Promise.reject(new Error("offline")));
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ regenerateSar })}
        createStream={harness.factory}
      />,
    );
    runToCompletion(harness);
    harness.emit("step.shap.completed", {
      topFeatures: [{ feature: "amount", value: 1, shapValue: 0.5 }],
    });
    harness.emit("step.rag.completed", { mode: "vector", citations: [] });
    harness.emit("sar.started", {}, "6");
    harness.emit("sar.token", { token: "Original narrative under review." });
    harness.emit("run.completed", { riskScore: 0.8, riskBand: "high", sarDraftId: "s1" }, "7");

    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: "Regenerate" }));
      await Promise.resolve();
    });
    expect(regenerateSar).toHaveBeenCalledWith("run-1");
    // The draft is preserved and the button returns to its idle, clickable state.
    expect(screen.getByText(/Original narrative under review\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Regenerate" })).toBeEnabled();
  });

  it("hides Regenerate from auditor sessions", async () => {
    signOut();
    signInAs("auditor");
    const harness = streamHarness();
    const regenerateSar = vi.fn(() => Promise.resolve(sarDraft({ version: 2 })));
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ regenerateSar })}
        createStream={harness.factory}
      />,
    );
    runToCompletion(harness);
    harness.emit("step.shap.completed", {
      topFeatures: [{ feature: "amount", value: 1, shapValue: 0.5 }],
    });
    harness.emit("step.rag.completed", {
      mode: "vector",
      citations: [{ citation: "c", title: "t", source: "FinCEN", snippet: "s" }],
    });
    harness.emit("sar.started", {}, "6");
    harness.emit("sar.token", { token: "Original narrative under review." });
    harness.emit("run.completed", { riskScore: 0.8, riskBand: "high", sarDraftId: "s1" }, "7");

    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));

    expect(screen.queryByRole("button", { name: "Regenerate" })).not.toBeInTheDocument();
    expect(regenerateSar).not.toHaveBeenCalled();
  });

  it("shows the failed state and blocks submit when the auto-run fails", () => {
    const harness = streamHarness();
    render(<Investigation runId="run-1" client={makeClient()} createStream={harness.factory} />);
    harness.emit("run.started", { transactionId: "tx-1" }, "1");
    harness.emit("run.failed", { code: "scoring_unavailable" }, "9");
    expect(screen.getByText("Investigation failed")).toBeInTheDocument();
    expect(screen.getByText("Auto-run failed")).toBeInTheDocument();
  });

  it("labels a non-z-score driver without a sigma and shows the pre-completion risk chip", () => {
    const harness = streamHarness();
    render(<Investigation runId="run-1" client={makeClient()} createStream={harness.factory} />);
    harness.emit("run.started", { transactionId: "tx-1" }, "1");
    harness.emit("step.scoring.completed", { fraudProbability: 0.9 }, "3");
    // Before completion there is no band yet — the chip falls back to the bare probability.
    expect(screen.getByText("Risk · 0.90")).toBeInTheDocument();
    harness.emit("step.shap.completed", {
      topFeatures: [{ feature: "country_risk", value: 0.8, shapValue: 0.3 }],
    });
    expect(screen.getByText("Top driver: Country Risk 0.8")).toBeInTheDocument();
  });

  it("steps back to the previous evidence", async () => {
    const harness = streamHarness();
    render(<Investigation runId="run-1" client={makeClient()} createStream={harness.factory} />);
    runToCompletion(harness);
    harness.emit("step.shap.completed", {
      topFeatures: [{ feature: "amount_zscore", value: 4.1, shapValue: 0.5 }],
    });

    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    expect(screen.getByText(/Review the model drivers/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /back to risk/i }));
    expect(screen.getByText(/Confirm the risk assessment/)).toBeInTheDocument();
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

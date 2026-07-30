import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import type { SarDraftView } from "../lib/api";
import { signIn, signOut, type UserRole } from "../lib/session";
import type { SseClientOptions, SseHandle } from "../lib/sse";
import { notify, notifyError } from "../lib/toast";
import { demoPersona, makeClient, sarDraft, snapshot } from "../test/factories";
import { Investigation } from "./Investigation";

function signInAs(role: UserRole): void {
  const persona = demoPersona(role);
  signIn(persona.email, false, persona.role);
}

beforeEach(() => {
  signInAs("reviewer");
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

function emitReadyRun(
  harness: ReturnType<typeof streamHarness>,
  alertId: string | null = "alert-1",
  sarStatus = "draft",
): void {
  runToCompletion(harness);
  harness.emit("step.shap.completed", {
    topFeatures: [{ feature: "amount", value: 1, shapValue: 0.5 }],
  });
  harness.emit("step.rag.completed", {
    mode: "vector",
    citations: [{ citation: "c", title: "t", source: "FinCEN", snippet: "s" }],
  });
  harness.emit("sar.started", {}, "6");
  harness.emit("sar.token", { token: "Narrative ready for review." });
  harness.emit(
    "run.completed",
    {
      riskScore: 0.87,
      riskBand: "high",
      sarDraftId: "s1",
      sarStatus,
      alertId,
    },
    "7",
  );
}

function emitNoAlertRun(harness: ReturnType<typeof streamHarness>): void {
  runToCompletion(harness);
  harness.emit("step.shap.completed", {
    topFeatures: [{ feature: "amount_log", value: 9.9, shapValue: -0.4 }],
  });
  // Compatibility: an older backend may have enriched before returning alertId=null. The
  // completed no-alert UI must suppress that stale enrichment rather than imply relevance.
  harness.emit("step.rag.completed", {
    mode: "vector",
    citations: [{ citation: "legacy", title: "legacy", source: "FinCEN", snippet: "legacy" }],
  });
  harness.emit(
    "run.completed",
    { riskScore: 0.22, riskBand: "low", sarDraftId: null, sarStatus: null, alertId: null },
    "5",
  );
}

async function advanceToApproval(): Promise<void> {
  await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
  await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
  await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));
  await userEvent.click(screen.getByRole("button", { name: /continue to approval/i }));
}

describe("Investigation", () => {
  it("streams the auto-run and walks the wizard from risk to submit", async () => {
    const harness = streamHarness();
    let resolveReview: (draft: SarDraftView) => void = () => undefined;
    const reviewSar = vi.fn(
      () =>
        new Promise<SarDraftView>((resolve) => {
          resolveReview = resolve;
        }),
    );
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ reviewSar })}
        createStream={harness.factory}
      />,
    );
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
    harness.emit(
      "run.completed",
      {
        riskScore: 0.87,
        riskBand: "high",
        sarDraftId: "s1",
        sarStatus: "draft",
        alertId: "alert-1",
      },
      "7",
    );
    expect(harness.close).toHaveBeenCalled();

    // Evidence chips report signed SHAP direction/contribution, not the transformed raw value.
    expect(screen.getByText("Risk: High · 0.87")).toBeInTheDocument();
    expect(screen.getByText("Top risk driver: Amount Zscore · SHAP +0.500")).toBeInTheDocument();
    expect(screen.getByText("Auto-run complete")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    expect(screen.getByText("amount_zscore")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
    expect(screen.getByText("SAR filing")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));
    expect(screen.getByText(/On 22 June 2026/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /continue to approval/i }));
    expect(screen.getByText("Ready for internal approval")).toBeInTheDocument();
    expect(screen.getByText("Step 5 of 5")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Approve SAR" }));
    expect(reviewSar).toHaveBeenCalledWith("alert-1", { decision: "approve" });
    expect(screen.getByRole("button", { name: /Approving/i })).toBeDisabled();
    expect(notify).not.toHaveBeenCalled();
    expect(window.location.hash).not.toBe("#/alerts");

    await act(async () => {
      resolveReview(sarDraft({ status: "approved" }));
      await Promise.resolve();
    });
    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({ tone: "positive", title: "SAR approved" }),
    );
    expect(window.location.hash).toBe("#/alerts");
  });

  it("keeps the reviewer on the report and shows an error toast when approval fails", async () => {
    const harness = streamHarness();
    const error = new Error("offline");
    const reviewSar = vi.fn(() => Promise.reject(error));
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ reviewSar })}
        createStream={harness.factory}
      />,
    );
    emitReadyRun(harness);
    await advanceToApproval();

    await userEvent.click(screen.getByRole("button", { name: "Approve SAR" }));

    await waitFor(() => expect(notifyError).toHaveBeenCalledWith(error));
    expect(notify).not.toHaveBeenCalledWith(expect.objectContaining({ tone: "positive" }));
    expect(window.location.hash).not.toBe("#/alerts");
    expect(screen.getByRole("button", { name: "Approve SAR" })).toBeEnabled();
  });

  it("shows a compact no-alert outcome without RAG, SAR, or approval controls", async () => {
    const harness = streamHarness();
    const reviewSar = vi.fn(() => Promise.resolve(sarDraft({ status: "approved" })));
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ reviewSar })}
        createStream={harness.factory}
      />,
    );
    emitNoAlertRun(harness);
    expect(screen.getByText("INV-1")).toBeInTheDocument();
    expect(screen.getByText("Review the analysis")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to outcome/i }));

    expect(screen.getByText("Analysis complete — no alert")).toBeInTheDocument();
    expect(
      screen.getByText(/stopped before regulatory retrieval and SAR drafting/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Step 3 of 3")).toBeInTheDocument();
    expect(screen.queryByText("Citations")).not.toBeInTheDocument();
    expect(screen.queryByText(/regulatory citation/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve SAR" })).not.toBeInTheDocument();
    expect(reviewSar).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Back to transactions" }));
    expect(window.location.hash).toBe("#/transactions");
  });

  it("disables filing when the persisted SAR draft is not approvable", async () => {
    const harness = streamHarness();
    const reviewSar = vi.fn(() => Promise.resolve(sarDraft({ status: "approved" })));
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ reviewSar })}
        createStream={harness.factory}
      />,
    );
    emitReadyRun(harness, "alert-1", "failed");
    await advanceToApproval();

    expect(screen.getByText(/no draft that is eligible for approval/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve SAR" })).toBeDisabled();
    expect(reviewSar).not.toHaveBeenCalled();
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
    harness.emit(
      "run.completed",
      { riskScore: 0.87, riskBand: "high", sarDraftId: "s1", sarStatus: "draft", alertId: "a1" },
      "7",
    );

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
    expect(screen.getByRole("button", { name: /continue to approval/i })).toBeDisabled();

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
    harness.emit(
      "run.completed",
      { riskScore: 0.8, riskBand: "high", sarDraftId: "s1", sarStatus: "draft", alertId: "a1" },
      "7",
    );

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
    harness.emit(
      "run.completed",
      { riskScore: 0.8, riskBand: "high", sarDraftId: "s1", sarStatus: "draft", alertId: "a1" },
      "7",
    );

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

  it("labels signed SHAP drivers and shows the pre-completion risk chip", () => {
    const harness = streamHarness();
    render(<Investigation runId="run-1" client={makeClient()} createStream={harness.factory} />);
    harness.emit("run.started", { transactionId: "tx-1" }, "1");
    harness.emit("step.scoring.completed", { fraudProbability: 0.9 }, "3");
    // Before completion there is no band yet — the chip falls back to the bare probability.
    expect(screen.getByText("Risk · 0.90")).toBeInTheDocument();
    harness.emit("step.shap.completed", {
      topFeatures: [{ feature: "country_risk", value: 0.8, shapValue: 0.3 }],
    });
    expect(screen.getByText("Top risk driver: Country Risk · SHAP +0.300")).toBeInTheDocument();
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

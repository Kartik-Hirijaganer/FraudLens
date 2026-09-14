import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import type { SarDraftView } from "../lib/api";
import { signIn, signOut, type UserRole } from "../lib/session";
import { notify } from "../lib/toast";
import { demoPersona, makeClient, sarDraft, snapshot } from "../test/factories";
import { emitNoAlertRun, runToCompletion, streamHarness } from "../test/streamHarness";
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

describe("Investigation wizard", () => {
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
    expect(screen.getByText("Machine progress")).toBeInTheDocument();
    expect(screen.getByText("Single-writer")).toBeInTheDocument();
    expect(document.querySelectorAll('[aria-live="polite"]')).toHaveLength(1);
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
    expect(screen.getByText("Amount zscore")).toBeInTheDocument();

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

  it("shows the failed state and blocks submit when the auto-run fails", () => {
    const harness = streamHarness();
    render(<Investigation runId="run-1" client={makeClient()} createStream={harness.factory} />);
    harness.emit("run.started", { transactionId: "tx-1" }, "1");
    harness.emit("run.failed", { code: "scoring_unavailable" }, "9");
    expect(screen.getByText("Investigation failed")).toBeInTheDocument();
    expect(screen.getByText("Auto-run failed")).toBeInTheDocument();
  });

  it("keeps an agent failure in the timeline without promoting it to a run error", () => {
    const harness = streamHarness();
    render(<Investigation runId="run-1" client={makeClient()} createStream={harness.factory} />);
    harness.emit("run.started", { transactionId: "tx-1" }, "1");
    harness.emit("agent.started", {
      agentRunId: "writer-1",
      agent: "sar_writer",
      attempt: 1,
      status: "started",
    });
    harness.emit("agent.completed", {
      agentRunId: "writer-1",
      agent: "sar_writer",
      attempt: 1,
      status: "failed",
      errorCode: "writer_schema_invalid",
    });

    expect(screen.getByText("4-agent review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /SAR writer/ })).toHaveTextContent("Failed");
    expect(screen.queryByText("Investigation failed")).not.toBeInTheDocument();
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

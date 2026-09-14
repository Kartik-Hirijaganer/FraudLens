import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import type { SarDraftView } from "../lib/api";
import { signIn, signOut, type UserRole } from "../lib/session";
import { notify, notifyError } from "../lib/toast";
import { demoPersona, makeClient, sarDraft, snapshot } from "../test/factories";
import { emitReadyRun, runToCompletion, streamHarness } from "../test/streamHarness";
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

async function advanceToApproval(): Promise<void> {
  await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
  await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
  await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));
  await userEvent.click(screen.getByRole("button", { name: /continue to approval/i }));
}

describe("Investigation SAR review", () => {
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
    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));

    expect(screen.getByText("Drafting blocked")).toBeInTheDocument();
    expect(
      screen.getByText(/risk score, rules, and model drivers remain available/i),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /continue to approval/i })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Approve SAR" })).not.toBeInTheDocument();
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

  it("hydrates persisted SAR content after a completed replay with no token events", async () => {
    const harness = streamHarness();
    const getInvestigation = vi.fn(() =>
      Promise.resolve(snapshot({ sarContent: "Persisted narrative restored after replay." })),
    );
    render(
      <Investigation
        runId="run-1"
        client={makeClient({ getInvestigation })}
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

    await waitFor(() => expect(getInvestigation).toHaveBeenCalledWith("run-1"));
    await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
    await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));
    expect(screen.getByText(/Persisted narrative restored after replay/)).toBeInTheDocument();
  });
});

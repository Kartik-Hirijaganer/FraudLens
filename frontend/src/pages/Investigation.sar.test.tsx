import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  alertDetail,
  alertView,
  caseCapabilities,
  makeClient,
  sarDraft,
  snapshot,
} from "../test/factories";
import { emitReadyRun, runToCompletion, streamHarness } from "../test/streamHarness";
import { Investigation } from "./Investigation";
afterEach(() => {
  window.location.hash = "";
});
describe("Investigation SAR evidence", () => {
  it.each(["draft", "submitted", "approved", "rejected", "failed"] as const)(
    "keeps %s evidence and one route into the case",
    async (status) => {
      const harness = streamHarness();
      const client = makeClient({
        getInvestigation: () => Promise.resolve(snapshot({ sarStatus: status })),
        getAlert: () =>
          Promise.resolve(
            alertDetail({
              alert: alertView({ capabilities: caseCapabilities([]) }),
              sarDraft: sarDraft({ status }),
            }),
          ),
      });
      render(<Investigation runId="run-1" client={client} createStream={harness.factory} />);
      emitReadyRun(harness, "alert-1", status);
      await userEvent.click(screen.getByRole("button", { name: /continue to drivers/i }));
      await userEvent.click(screen.getByRole("button", { name: /continue to citations/i }));
      await userEvent.click(screen.getByRole("button", { name: /continue to sar draft/i }));
      expect(screen.queryByRole("button", { name: "Approve SAR" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Regenerate" })).not.toBeInTheDocument();
      if (["approved", "rejected"].includes(status)) {
        expect(screen.getByText(`SAR ${status}`)).toBeVisible();
        expect(screen.getByText(/the recorded decision is final/i)).toBeVisible();
      }
      expect(
        screen.queryByRole("button", { name: "Continue preparation in case" }),
      ).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: /continue to case/i }));
      await userEvent.click(screen.getByRole("button", { name: "Open case" }));
      expect(window.location.hash).toBe("#/alerts/alert-1");
      expect(client.decideSubmission).not.toHaveBeenCalled();
      expect(client.regenerateSar).not.toHaveBeenCalled();
    },
  );
  it("keeps unsaved inline changes on the SAR step until saved or cancelled", async () => {
    const user = userEvent.setup();
    const harness = streamHarness();
    const client = makeClient({
      getAlert: () =>
        Promise.resolve(
          alertDetail({
            alert: alertView({ status: "in_review", capabilities: caseCapabilities(["edit"]) }),
          }),
        ),
    });
    render(<Investigation runId="run-1" client={client} createStream={harness.factory} />);
    emitReadyRun(harness, "alert-1", "draft");
    await user.click(screen.getByRole("button", { name: /continue to drivers/i }));
    await user.click(screen.getByRole("button", { name: /continue to citations/i }));
    await user.click(screen.getByRole("button", { name: /continue to sar draft/i }));
    await user.click(await screen.findByRole("button", { name: "Edit SAR" }));
    await user.type(screen.getByLabelText("Working SAR narrative"), " Analyst clarification.");
    expect(screen.getByRole("button", { name: /continue to case/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /back to citations/i })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: /continue to case/i })).toBeEnabled();
    expect(client.saveSar).not.toHaveBeenCalled();
  });

  it("hydrates persisted SAR content after a completed replay with no token events", async () => {
    const harness = streamHarness();
    const getInvestigation = vi.fn(() =>
      Promise.resolve(snapshot({ sarContent: "Persisted narrative restored after replay." })),
    );
    render(
      <Investigation
        runId="run-1"
        client={makeClient({
          getInvestigation,
          getAlert: () =>
            Promise.resolve(
              alertDetail({
                sarDraft: sarDraft({ content: "Persisted narrative restored after replay." }),
              }),
            ),
        })}
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

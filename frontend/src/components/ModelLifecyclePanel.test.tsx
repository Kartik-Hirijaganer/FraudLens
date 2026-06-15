import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { deployment, driftReport, modelVersion } from "../test/factories";
import { ModelLifecyclePanel } from "./ModelLifecyclePanel";

function handlers() {
  return {
    onTriggerTraining: vi.fn(),
    onPromoteShadow: vi.fn(),
    onApprove: vi.fn(),
    onSetCanary: vi.fn(),
    onRollback: vi.fn(),
    onEvaluateCanary: vi.fn(),
  };
}

describe("ModelLifecyclePanel", () => {
  it("renders deployment + retrain/evaluate/rollback and empty registry/drift", async () => {
    const callbacks = handlers();
    render(
      <ModelLifecyclePanel
        versions={[]}
        deployment={deployment({ canaryVersionLabel: "model-v2", canaryPercent: 25 })}
        driftReports={[]}
        {...callbacks}
      />,
    );
    expect(screen.getByText(/model-v2 @ 25%/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retrain candidate" }));
    expect(callbacks.onTriggerTraining).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Evaluate canary" }));
    expect(callbacks.onEvaluateCanary).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Roll back" }));
    expect(callbacks.onRollback).toHaveBeenCalled();
    expect(screen.getByText("No model versions")).toBeInTheDocument();
    expect(screen.getByText("No drift reports")).toBeInTheDocument();
  });

  it("hides evaluate and shows no-deployment text without a canary", () => {
    render(
      <ModelLifecyclePanel versions={[]} deployment={null} driftReports={[]} {...handlers()} />,
    );
    expect(screen.getByText("No deployment is configured yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Evaluate canary" })).not.toBeInTheDocument();
  });

  it("offers status-appropriate actions for candidate and shadow versions", async () => {
    const callbacks = handlers();
    render(
      <ModelLifecyclePanel
        versions={[
          modelVersion({ versionId: "v-cand", status: "candidate", metrics: { prAuc: 0.84 } }),
          modelVersion({ versionId: "v-shad", status: "shadow", metrics: {} }),
        ]}
        deployment={deployment()}
        driftReports={[driftReport({ severity: "low" })]}
        {...callbacks}
      />,
    );
    expect(screen.getByText(/PR-AUC 0\.840/)).toBeInTheDocument();
    expect(screen.getByText("Low")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Promote to shadow" }));
    expect(callbacks.onPromoteShadow).toHaveBeenCalledWith("v-cand");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(callbacks.onApprove).toHaveBeenCalledWith("v-shad");
    await userEvent.click(screen.getByRole("button", { name: "Activate (100%)" }));
    expect(callbacks.onSetCanary).toHaveBeenCalledWith("v-shad", 100);
  });

  it("offers ramp actions for a canary version and none for an active one", async () => {
    const callbacks = handlers();
    render(
      <ModelLifecyclePanel
        versions={[
          modelVersion({ versionId: "v-can", status: "canary" }),
          modelVersion({ versionId: "v-act", status: "active" }),
        ]}
        deployment={deployment()}
        driftReports={[]}
        {...callbacks}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Canary 5%" }));
    expect(callbacks.onSetCanary).toHaveBeenCalledWith("v-can", 5);
  });
});

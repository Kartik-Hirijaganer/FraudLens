import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import { ApiError } from "../lib/api";
import { notify } from "../lib/toast";
import { deployment, makeClient, modelVersion } from "../test/factories";
import { ModelAdmin } from "./ModelAdmin";

describe("ModelAdmin", () => {
  it("retrains and drives candidate → shadow → approve → activate", async () => {
    const client = makeClient({
      listModelVersions: vi.fn(() =>
        Promise.resolve({
          versions: [
            modelVersion({ versionId: "v1", status: "candidate" }),
            modelVersion({ versionId: "v2", status: "shadow" }),
          ],
          activeVersionLabel: "model-v1",
        }),
      ),
    });
    render(<ModelAdmin client={client} />);
    await screen.findByRole("button", { name: "Retrain candidate" });

    await userEvent.click(screen.getByRole("button", { name: "Retrain candidate" }));
    await waitFor(() => expect(client.triggerTraining).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "Promote to shadow" }));
    await waitFor(() => expect(client.promoteToShadow).toHaveBeenCalledWith("v1"));
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(client.approveVersion).toHaveBeenCalledWith("v2"));
    await userEvent.click(screen.getByRole("button", { name: "Activate (100%)" }));
    await waitFor(() => expect(client.setCanary).toHaveBeenCalledWith("v2", 100));
  });

  it("evaluates and rolls back a canary deployment", async () => {
    const client = makeClient({
      getDeployment: vi.fn(() =>
        Promise.resolve(deployment({ canaryVersionLabel: "model-v2", canaryPercent: 25 })),
      ),
    });
    render(<ModelAdmin client={client} />);
    await screen.findByText(/model-v2 @ 25%/);
    await userEvent.click(screen.getByRole("button", { name: "Evaluate canary" }));
    await waitFor(() => expect(client.evaluateCanary).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "Roll back" }));
    await waitFor(() => expect(client.rollbackDeployment).toHaveBeenCalled());
  });

  it("warns when canary evaluation auto-aborts", async () => {
    const client = makeClient({
      getDeployment: vi.fn(() =>
        Promise.resolve(deployment({ canaryVersionLabel: "model-v2", canaryPercent: 25 })),
      ),
      evaluateCanary: vi.fn(() =>
        Promise.resolve({
          aborted: true,
          activeCount: 10,
          activeMean: 0.2,
          canaryCount: 10,
          canaryMean: 0.31,
          deviation: 0.11,
          deployment: deployment(),
        }),
      ),
    });
    render(<ModelAdmin client={client} />);
    await screen.findByText(/model-v2 @ 25%/);
    await userEvent.click(screen.getByRole("button", { name: "Evaluate canary" }));
    await waitFor(() =>
      expect(notify).toHaveBeenCalledWith({
        tone: "warning",
        title: "Canary auto-aborted",
        description: "deviation 0.110",
      }),
    );
  });

  it("shows an unconfigured deployment when the deployment pointer is missing", async () => {
    const client = makeClient({
      getDeployment: vi.fn(() =>
        Promise.reject(new ApiError(404, "deployment_not_found", "missing")),
      ),
    });
    render(<ModelAdmin client={client} />);
    expect(await screen.findByText("No deployment is configured yet.")).toBeInTheDocument();
  });

  it("shows an error state when the registry fails to load", async () => {
    const client = makeClient({
      listModelVersions: vi.fn(() =>
        Promise.reject(new ApiError(403, "admin_role_required", "no")),
      ),
    });
    render(<ModelAdmin client={client} />);
    expect(await screen.findByText("Admin only")).toBeInTheDocument();
  });
});

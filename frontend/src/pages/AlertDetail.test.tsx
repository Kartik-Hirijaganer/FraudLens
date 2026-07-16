import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import { ApiError } from "../lib/api";
import { DEMO_ROLES, signIn, signOut, type UserRole } from "../lib/session";
import { alertDetail, alertView, makeClient } from "../test/factories";
import { AlertDetail } from "./AlertDetail";

function signInAs(role: UserRole): void {
  const demoRole = DEMO_ROLES.find((candidate) => candidate.role === role);
  if (!demoRole) {
    throw new Error(`Missing demo role: ${role}`);
  }
  signIn(demoRole.email, false, demoRole.role);
}

afterEach(() => {
  signOut();
  vi.clearAllMocks();
});

describe("AlertDetail", () => {
  it("runs the SAR review decisions and shows the activity history", async () => {
    signInAs("reviewer");
    const client = makeClient({
      getAlert: vi.fn(() =>
        Promise.resolve(
          alertDetail({
            actions: [
              {
                actionId: "act-1",
                action: "resolve",
                actorId: "u1",
                note: "needs a closer look",
                fromStatus: "in_review",
                toStatus: "resolved",
                createdAt: "2026-06-11T11:00:00Z",
              },
            ],
          }),
        ),
      ),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByText(/Suspicious structuring activity observed/)).toBeInTheDocument();
    expect(screen.getByText("needs a closer look")).toBeInTheDocument();
    expect(screen.getByText(/In Review → Completed/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(client.reviewSar).toHaveBeenCalledWith("alert-1", { decision: "approve" }),
    );

    await userEvent.type(screen.getByLabelText("Rejection reason"), "dup");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(client.reviewSar).toHaveBeenCalledWith("alert-1", {
        decision: "reject",
        reason: "dup",
      }),
    );

    await userEvent.type(screen.getByLabelText("Edit narrative"), "edited");
    await userEvent.click(screen.getByRole("button", { name: "Save edit" }));
    await waitFor(() =>
      expect(client.reviewSar).toHaveBeenCalledWith("alert-1", {
        decision: "edit",
        editedContent: "edited",
      }),
    );
  });

  it("runs the triage actions including resolve-with-label", async () => {
    signInAs("reviewer");
    const client = makeClient();
    render(<AlertDetail alertId="alert-1" client={client} />);
    await screen.findByText(/Suspicious structuring activity observed/);

    await userEvent.click(screen.getByRole("button", { name: "Escalate" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "escalate",
        note: undefined,
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Comment" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "comment",
        note: undefined,
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "dismiss",
        note: undefined,
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "resolve",
        label: "confirmed_fraud",
        note: undefined,
      }),
    );
  });

  it("shows only analyst-permitted actions", async () => {
    signInAs("analyst");
    const client = makeClient();
    render(<AlertDetail alertId="alert-1" client={client} />);
    await screen.findByText(/Suspicious structuring activity observed/);

    expect(screen.getByRole("button", { name: "Comment" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send for review" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
    expect(screen.getByText("Awaiting reviewer approval.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resolve" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Resolution label")).not.toBeInTheDocument();
  });

  it("shows a read-only action rail for auditor sessions", async () => {
    signInAs("auditor");
    const client = makeClient();
    render(<AlertDetail alertId="alert-1" client={client} />);
    await screen.findByText(/Suspicious structuring activity observed/);

    expect(screen.getByText(/Read-only access/)).toBeInTheDocument();
    expect(screen.getByText("Awaiting reviewer approval.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Note (optional)")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Comment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("shows an empty SAR state when there is no draft", async () => {
    signInAs("reviewer");
    const client = makeClient({
      getAlert: vi.fn(() => Promise.resolve(alertDetail({ sarDraft: null }))),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByText("No SAR draft")).toBeInTheDocument();
  });

  it("labels only seeded alert details as sample data", async () => {
    signInAs("reviewer");
    const seededClient = makeClient({
      getAlert: vi.fn(() =>
        Promise.resolve(alertDetail({ alert: alertView({ origin: "seed" }), sarDraft: null })),
      ),
    });
    const seeded = render(<AlertDetail alertId="seed-alert" client={seededClient} />);
    expect(await screen.findByText("Sample data")).toBeInTheDocument();
    seeded.unmount();

    render(<AlertDetail alertId="pipeline-alert" client={makeClient()} />);
    await screen.findByText(/Suspicious structuring activity observed/);
    expect(screen.queryByText("Sample data")).not.toBeInTheDocument();
  });

  it("shows an error state when the alert fails to load", async () => {
    signInAs("reviewer");
    const client = makeClient({
      getAlert: vi.fn(() => Promise.reject(new ApiError(404, "alert_not_found", "missing"))),
    });
    render(<AlertDetail alertId="x" client={client} />);
    expect(await screen.findByText("Alert not found")).toBeInTheDocument();
  });
});

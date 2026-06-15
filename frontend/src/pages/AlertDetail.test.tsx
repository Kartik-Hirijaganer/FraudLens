import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import { ApiError } from "../lib/api";
import { alertDetail, makeClient } from "../test/factories";
import { AlertDetail } from "./AlertDetail";

describe("AlertDetail", () => {
  it("runs the SAR review decisions and shows the activity history", async () => {
    const client = makeClient({
      getAlert: vi.fn(() =>
        Promise.resolve(
          alertDetail({
            actions: [
              {
                actionId: "act-1",
                action: "escalate",
                actorId: "u1",
                note: "needs a closer look",
                fromStatus: "open",
                toStatus: "in_review",
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
    await userEvent.click(screen.getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "resolve",
        label: "confirmed_fraud",
        note: undefined,
      }),
    );
  });

  it("shows an empty SAR state when there is no draft", async () => {
    const client = makeClient({
      getAlert: vi.fn(() => Promise.resolve(alertDetail({ sarDraft: null }))),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByText("No SAR draft")).toBeInTheDocument();
  });

  it("shows an error state when the alert fails to load", async () => {
    const client = makeClient({
      getAlert: vi.fn(() => Promise.reject(new ApiError(404, "alert_not_found", "missing"))),
    });
    render(<AlertDetail alertId="x" client={client} />);
    expect(await screen.findByText("Alert not found")).toBeInTheDocument();
  });
});

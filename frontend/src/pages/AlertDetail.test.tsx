import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import { ApiError } from "../lib/api";
import { signIn, signOut, type UserRole } from "../lib/session";
import { agentRun, alertDetail, alertView, demoPersona, makeClient } from "../test/factories";
import { AlertDetail } from "./AlertDetail";

function signInAs(role: UserRole): void {
  const persona = demoPersona(role);
  signIn(persona.email, false, persona.role);
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

    await userEvent.click(screen.getByRole("button", { name: "Approve SAR" }));
    await waitFor(() =>
      expect(client.reviewSar).toHaveBeenCalledWith("alert-1", { decision: "approve" }),
    );

    await userEvent.type(screen.getByLabelText("Reason for rejection"), "dup");
    await userEvent.click(screen.getByRole("button", { name: "Reject SAR" }));
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

  it("renders the SAR narrative as formatted, analyst-friendly content", async () => {
    signInAs("reviewer");
    const client = makeClient({
      getAlert: vi.fn(() =>
        Promise.resolve(
          alertDetail({
            sarDraft: {
              ...alertDetail().sarDraft!,
              content:
                "# Suspicious Activity Report\n\n**Subject:** Suspected ach activity\n\n" +
                "## Risk indicators\n\nRules fired: rapid_movement (rapid_movement). " +
                "Model drivers: amount_log.",
            },
          }),
        ),
      ),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Suspicious Activity Report" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Risk indicators" })).toBeInTheDocument();
    expect(screen.getByText("Subject:").tagName).toBe("STRONG");
    expect(screen.getByText(/Rapid movement/)).toBeInTheDocument();
    expect(screen.getByText(/Transaction amount \(log scale\)/)).toBeInTheDocument();
    expect(screen.queryByText(/##|rapid_movement|amount_log/)).not.toBeInTheDocument();
  });

  it("keeps investigation updates separate from the final case outcome", async () => {
    signInAs("reviewer");
    const client = makeClient({
      getAlert: vi.fn(() =>
        Promise.resolve(
          alertDetail({ sarDraft: { ...alertDetail().sarDraft!, status: "approved" } }),
        ),
      ),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    await screen.findByText(/Suspicious structuring activity observed/);

    await userEvent.click(screen.getByRole("button", { name: "Escalate for review" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "escalate",
        note: undefined,
      }),
    );
    await userEvent.type(screen.getByLabelText("Investigation note (optional)"), "checked");
    await userEvent.click(screen.getByRole("button", { name: "Add note" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "comment",
        note: "checked",
      }),
    );
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close alert" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "resolve",
        label: "confirmed_fraud",
        note: "checked",
      }),
    );
  });

  it("requires the SAR decision before showing a final outcome", async () => {
    signInAs("reviewer");
    render(<AlertDetail alertId="alert-1" client={makeClient()} />);
    expect(await screen.findByText(/Complete step 1/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Final outcome")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close alert" })).not.toBeInTheDocument();
  });

  it("filters final outcomes to the recorded SAR decision", async () => {
    signInAs("reviewer");
    const approved = render(
      <AlertDetail
        alertId="approved-alert"
        client={makeClient({
          getAlert: vi.fn(() =>
            Promise.resolve(
              alertDetail({ sarDraft: { ...alertDetail().sarDraft!, status: "approved" } }),
            ),
          ),
        })}
      />,
    );
    const approvedSelect = await screen.findByLabelText("Final outcome");
    expect(
      Array.from(approvedSelect.querySelectorAll("option"), (option) => option.textContent),
    ).toEqual(["Confirmed fraud", "False negative"]);
    expect(screen.queryByRole("button", { name: "Approve SAR" })).not.toBeInTheDocument();
    approved.unmount();

    render(
      <AlertDetail
        alertId="rejected-alert"
        client={makeClient({
          getAlert: vi.fn(() =>
            Promise.resolve(
              alertDetail({ sarDraft: { ...alertDetail().sarDraft!, status: "rejected" } }),
            ),
          ),
        })}
      />,
    );
    const rejectedSelect = await screen.findByLabelText("Final outcome");
    expect(
      Array.from(rejectedSelect.querySelectorAll("option"), (option) => option.textContent),
    ).toEqual(["False positive", "Benign"]);
  });

  it("hides mutation controls after the case closes", async () => {
    signInAs("reviewer");
    const client = makeClient({
      getAlert: vi.fn(() =>
        Promise.resolve(alertDetail({ alert: alertView({ status: "resolved" }) })),
      ),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByText("Case closed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open the investigation run" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve SAR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close alert" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows shared production provenance and opens the originating investigation", async () => {
    signInAs("reviewer");
    const client = makeClient({
      getAlert: vi.fn(() =>
        Promise.resolve(
          alertDetail({
            workflowMode: "multi_agent",
            graphVersion: "agents-v1",
            agentExecutions: [agentRun()],
          }),
        ),
      ),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);

    expect(await screen.findByText("How this SAR was produced")).toBeInTheDocument();
    expect(screen.getByText("4-agent review")).toBeInTheDocument();
    expect(screen.getByText("Recorded")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Open the investigation run" }));
    expect(window.location.hash).toBe("#/investigations/run-1");
  });

  it("shows only analyst-permitted actions", async () => {
    signInAs("analyst");
    const client = makeClient();
    render(<AlertDetail alertId="alert-1" client={client} />);
    await screen.findByText(/Suspicious structuring activity observed/);

    expect(screen.getByRole("button", { name: "Add note" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send for review" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
    expect(screen.getByText("Awaiting reviewer decision.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve SAR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject SAR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close alert" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Final outcome")).not.toBeInTheDocument();
  });

  it("names the assigned reviewer in the alert summary", async () => {
    signInAs("analyst");
    const client = makeClient({
      getAlert: vi.fn(() =>
        Promise.resolve(
          alertDetail({
            alert: alertView({
              status: "in_review",
              assignedTo: "reviewer-1",
              assignedToName: "Demo Reviewer",
            }),
          }),
        ),
      ),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);

    expect(await screen.findByText("Assigned to")).toBeInTheDocument();
    expect(screen.getByText("Demo Reviewer")).toBeInTheDocument();
  });

  it("shows a read-only action rail for auditor sessions", async () => {
    signInAs("auditor");
    const client = makeClient();
    render(<AlertDetail alertId="alert-1" client={client} />);
    await screen.findByText(/Suspicious structuring activity observed/);

    expect(screen.getByText(/Read-only access/)).toBeInTheDocument();
    expect(screen.getByText("Awaiting reviewer decision.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Investigation note (optional)")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add note" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve SAR" })).not.toBeInTheDocument();
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

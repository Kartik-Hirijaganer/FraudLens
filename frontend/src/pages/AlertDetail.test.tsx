import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));
import { signIn, signOut } from "../lib/session";
import { notifyError } from "../lib/toast";
import {
  alertDetail,
  alertView,
  caseCapabilities,
  makeClient,
  sarDraft,
  submissionView,
} from "../test/factories";
import { AlertDetail } from "./AlertDetail";
import type { CaseAction } from "../lib/api";
function prepared(allowed: CaseAction[] = ["edit", "regenerate", "submit", "comment", "dismiss"]) {
  return alertDetail({
    alert: alertView({
      status: "in_review",
      caseVersion: 2,
      assignedToName: "Maker",
      capabilities: caseCapabilities(allowed),
    }),
  });
}
afterEach(() => {
  signOut();
  vi.clearAllMocks();
});
describe("Case workspace", () => {
  it("offers backend-authorized self pickup and reloads authoritative state", async () => {
    const client = makeClient();
    render(<AlertDetail alertId="alert-1" client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Take case" }));
    await waitFor(() =>
      expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
        action: "assign",
        expectedCaseVersion: 1,
      }),
    );
    expect(client.getAlert).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Approve SAR" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close alert" })).not.toBeInTheDocument();
  });
  it("saves a versioned narrative and prevents submitting unsaved changes", async () => {
    const client = makeClient({ getAlert: () => Promise.resolve(prepared()) });
    render(<AlertDetail alertId="alert-1" client={client} />);
    const editor = await screen.findByLabelText("Working SAR narrative");
    await userEvent.clear(editor);
    await userEvent.type(editor, "Revised narrative.");
    await userEvent.type(
      screen.getByLabelText("Submission or dismissal rationale"),
      "Investigated.",
    );
    expect(screen.getByRole("button", { name: "Send for review" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save narrative" }));
    expect(client.saveSar).toHaveBeenCalledWith("alert-1", {
      content: "Revised narrative.",
      expectedCaseVersion: 2,
      expectedSarVersion: 1,
    });
  });
  it.each(["sar", "no_sar"])(
    "submits %s with rationale, exact version and idempotency key",
    async (recommendation) => {
      const client = makeClient({ getAlert: () => Promise.resolve(prepared()) });
      render(<AlertDetail alertId="alert-1" client={client} />);
      await screen.findByLabelText("Working SAR narrative");
      await userEvent.selectOptions(screen.getByLabelText("Recommendation"), recommendation);
      await userEvent.type(
        screen.getByLabelText("Submission or dismissal rationale"),
        "Evidence checked.",
      );
      await userEvent.click(screen.getByRole("button", { name: "Send for review" }));
      expect(client.submitCase).toHaveBeenCalledWith(
        "alert-1",
        {
          expectedCaseVersion: 2,
          rationale: "Evidence checked.",
          recommendation,
          ...(recommendation === "sar" ? { sarVersion: 1 } : {}),
        },
        expect.any(String),
      );
    },
  );
  it("permits manual authoring after machine SAR failure without an approval shortcut", async () => {
    const client = makeClient({
      getAlert: () =>
        Promise.resolve({
          ...prepared(),
          sarDraft: null,
          sarContent: null,
          draftVersions: [],
        }),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    await userEvent.type(await screen.findByLabelText("Working SAR narrative"), "Manual evidence.");
    await userEvent.click(screen.getByRole("button", { name: "Save narrative" }));
    expect(client.saveSar).toHaveBeenCalledWith("alert-1", {
      content: "Manual evidence.",
      expectedCaseVersion: 2,
      expectedSarVersion: null,
    });
  });
  it("requires a rationale for dismissal and passes the case version", async () => {
    const client = makeClient({ getAlert: () => Promise.resolve(prepared()) });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByRole("button", { name: "Dismiss case" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Submission or dismissal rationale"), "No concern.");
    await userEvent.click(screen.getByRole("button", { name: "Dismiss case" }));
    expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
      action: "dismiss",
      note: "No concern.",
      expectedCaseVersion: 2,
    });
  });
  it("regenerates only through backend capabilities and carries the case version", async () => {
    const client = makeClient({ getAlert: () => Promise.resolve(prepared()) });
    render(<AlertDetail alertId="alert-1" client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Regenerate" }));
    expect(client.regenerateSar).toHaveBeenCalledWith("run-1", 2);
  });
  it("shows mandatory review blocks and keeps current evidence", async () => {
    const client = makeClient({
      getAlert: () => Promise.resolve(prepared(["edit", "submit", "comment"])),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByRole("button", { name: "Dismiss case" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Regenerate" })).toBeDisabled();
    expect(screen.getAllByText("Action blocked by case policy.").length).toBeGreaterThan(0);
    expect(screen.getByText("Risk evidence")).toBeVisible();
  });
  it("comments without submitting or changing the narrative", async () => {
    const client = makeClient();
    render(<AlertDetail alertId="alert-1" client={client} />);
    const comment = await screen.findByLabelText("Case comment");
    await userEvent.type(comment, "Clarify the rationale.");
    await userEvent.click(screen.getByRole("button", { name: "Add comment" }));
    expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
      action: "comment",
      note: "Clarify the rationale.",
      expectedCaseVersion: 1,
    });
    expect(client.saveSar).not.toHaveBeenCalled();
  });
  it.each(["approve", "return", "decline"] as const)(
    "checks a frozen round using %s and compatible outcome",
    async (decision) => {
      const client = makeClient({
        getAlert: () =>
          Promise.resolve(
            alertDetail({
              alert: alertView({
                status: "escalated",
                caseVersion: 3,
                capabilities: caseCapabilities(["approve", "return", "decline", "comment"]),
              }),
              sarDraft: sarDraft({ status: "submitted" }),
              submissions: [submissionView()],
            }),
          ),
      });
      render(<AlertDetail alertId="alert-1" client={client} />);
      await screen.findByLabelText("Review decision");
      expect(screen.queryByLabelText("Working SAR narrative")).not.toBeInTheDocument();
      await userEvent.selectOptions(screen.getByLabelText("Review decision"), decision);
      const button = screen.getByRole("button", {
        name:
          decision === "approve"
            ? "Approve submission"
            : decision === "return"
              ? "Return submission"
              : "Decline submission",
      });
      expect(button).toBeDisabled();
      await userEvent.type(
        screen.getByLabelText("Decision reason or required changes"),
        "Review complete.",
      );
      await userEvent.click(button);
      expect(client.decideSubmission).toHaveBeenCalledWith(
        "alert-1",
        "submission-1",
        {
          expectedCaseVersion: 3,
          decision,
          reason: "Review complete.",
          ...(decision === "return"
            ? {}
            : { outcome: decision === "approve" ? "confirmed_fraud" : "false_positive" }),
        },
        expect.any(String),
      );
    },
  );
  it("shows independent-person denial even when a user is an Admin", async () => {
    signIn("admin@test", false, "admin");
    const client = makeClient({
      getAlert: () =>
        Promise.resolve(
          alertDetail({
            alert: alertView({ status: "escalated", capabilities: caseCapabilities(["comment"]) }),
            submissions: [submissionView()],
          }),
        ),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByRole("button", { name: "Approve submission" })).toBeDisabled();
    expect(client.decideSubmission).not.toHaveBeenCalled();
  });
  it("displays returned changes, actors and separate immutable draft versions", async () => {
    const client = makeClient({
      getAlert: () =>
        Promise.resolve({
          ...prepared(),
          submissions: [
            submissionView({
              decision: "return",
              reason: "Add corroboration.",
              checkerAccountId: "checker",
              checkerPersonId: "checker-person",
            }),
          ],
          draftVersions: [
            sarDraft({ version: 2 }),
            sarDraft({ sarDraftId: "first", status: "returned" }),
          ],
        }),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByText("Add corroboration.")).toBeVisible();
    expect(screen.getByText(/Version 2/)).toBeVisible();
    expect(screen.getByText(/Version 1/)).toBeVisible();
    expect(screen.getByText(/checker-person/)).toBeVisible();
  });
  it("renders formatted SAR content and retained citations", async () => {
    const client = makeClient({
      getAlert: () =>
        Promise.resolve(
          alertDetail({
            sarDraft: sarDraft({
              content:
                "# Suspicious Activity Report\n\n**Subject:** wire transfer\n\n## Risk indicators\n\nRules fired: rapid_movement.",
            }),
          }),
        ),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(
      await screen.findByRole("heading", { name: "Suspicious Activity Report" }),
    ).toBeVisible();
    expect(screen.getByText("Subject:").tagName).toBe("STRONG");
    expect(screen.getByRole("heading", { name: "Risk indicators" })).toBeVisible();
  });
  it("preserves failed draft visibility and provides a no-SAR recommendation", async () => {
    const client = makeClient({
      getAlert: () =>
        Promise.resolve({
          ...prepared(),
          sarDraft: sarDraft({ status: "failed", content: "" }),
        }),
    });
    render(<AlertDetail alertId="alert-1" client={client} />);
    await screen.findByLabelText("Recommendation");
    expect(screen.getByText(/Author and save a usable SAR/)).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText("Recommendation"), "no_sar");
    await userEvent.type(screen.getByLabelText("Submission or dismissal rationale"), "No SAR.");
    expect(screen.getByRole("button", { name: "Send for review" })).toBeEnabled();
  });
  it("keeps a failed command reviewable and reuses its idempotency key", async () => {
    const submitCase = vi.fn(() => Promise.reject(new Error("Conflict — reload case")));
    const client = makeClient({ getAlert: () => Promise.resolve(prepared()), submitCase });
    render(<AlertDetail alertId="alert-1" client={client} />);
    await userEvent.type(
      await screen.findByLabelText("Submission or dismissal rationale"),
      "Evidence checked.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Send for review" }));
    await waitFor(() => expect(notifyError).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "Send for review" }));
    expect(submitCase.mock.calls[0]).toEqual(submitCase.mock.calls[1]);
    expect(screen.getByRole("button", { name: "Reload case" })).toBeEnabled();
  });
});

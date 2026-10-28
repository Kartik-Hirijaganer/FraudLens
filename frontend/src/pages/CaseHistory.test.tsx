import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  alertDetail,
  alertView,
  caseCapabilities,
  makeClient,
  sarDraft,
  submissionView,
} from "../test/factories";
import { signIn, signOut } from "../lib/session";
import { AlertDetail } from "./AlertDetail";
afterEach(() => {
  signOut();
  vi.restoreAllMocks();
});
it.each(["pending", "failed", "ready"] as const)(
  "shows truthful %s PDF state with permitted recovery",
  async (pdfStatus) => {
    signIn("reviewer@test", false, "reviewer");
    const client = makeClient({
      getAlert: () =>
        Promise.resolve(
          alertDetail({
            alert: alertView({ status: "resolved", capabilities: caseCapabilities() }),
            sarDraft: sarDraft({ status: "approved" }),
            submissions: [
              submissionView({
                decision: "approve",
                pdfStatus,
                reason: "Reviewed",
                outcome: "confirmed_fraud",
              }),
            ],
          }),
        ),
    });
    const create = vi.fn(() => "blob:pdf");
    URL.createObjectURL = create;
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<AlertDetail alertId="alert-1" client={client} />);
    expect(await screen.findByText(`PDF ${pdfStatus}`)).toBeVisible();
    const download = screen.getByRole("button", { name: "Download PDF" });
    if (pdfStatus === "ready") {
      await userEvent.click(download);
      expect(client.downloadSubmissionPdf).toHaveBeenCalledWith("alert-1", "submission-1");
      expect(create).toHaveBeenCalled();
    } else {
      expect(download).toBeDisabled();
      await userEvent.click(screen.getByRole("button", { name: "Retry PDF" }));
      expect(client.retrySubmissionPdf).toHaveBeenCalledWith("alert-1", "submission-1");
    }
    expect(screen.queryByLabelText("Working SAR narrative")).not.toBeInTheDocument();
  },
);
it("keeps declined no-SAR history read only without PDF access", async () => {
  const client = makeClient({
    getAlert: () =>
      Promise.resolve(
        alertDetail({
          alert: alertView({ status: "resolved", capabilities: caseCapabilities() }),
          sarDraft: null,
          submissions: [
            submissionView({
              decision: "decline",
              recommendation: "no_sar",
              sarDraft: null,
              reason: "Activity is benign",
              outcome: "benign",
            }),
          ],
        }),
      ),
  });
  render(<AlertDetail alertId="alert-1" client={client} />);
  expect(await screen.findByText("Activity is benign")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Download PDF" })).not.toBeInTheDocument();
});

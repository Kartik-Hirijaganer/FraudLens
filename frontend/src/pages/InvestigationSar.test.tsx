import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient, SarDraftView } from "../lib/api";
import { initialInvestigationState } from "../lib/investigation";
import { alertDetail, alertView, caseCapabilities, makeClient, sarDraft } from "../test/factories";
import { InvestigationSar } from "./InvestigationSar";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

function working(content = "Authoritative working narrative.", version = 3, caseVersion = 7) {
  return alertDetail({
    alert: alertView({
      status: "in_review",
      caseVersion,
      capabilities: caseCapabilities(["edit"]),
    }),
    sarDraft: sarDraft({ content, version }),
  });
}

function Editor({
  client,
  onSaved = vi.fn(),
}: {
  client: ApiClient;
  onSaved?: (draft: SarDraftView) => void;
}) {
  const [dirty, setDirty] = useState(false);
  return (
    <InvestigationSar
      alertId="alert-1"
      client={client}
      state={{ ...initialInvestigationState(), sarText: "Streamed evidence retained." }}
      dirty={dirty}
      onDirtyChange={setDirty}
      onSaved={onSaved}
    />
  );
}

describe("Inline investigation SAR editing", () => {
  it("opens a focused editor and saves the authoritative case/SAR versions", async () => {
    const user = userEvent.setup();
    const saved = sarDraft({ content: "Analyst-authored revision.", version: 4 });
    const getAlert = vi
      .fn()
      .mockResolvedValueOnce(working())
      .mockResolvedValue(working(saved.content, 4, 8));
    const client = makeClient({ getAlert, saveSar: vi.fn(() => Promise.resolve(saved)) });
    const onSaved = vi.fn();
    render(<Editor client={client} onSaved={onSaved} />);
    await user.click(await screen.findByRole("button", { name: "Edit SAR" }));
    const field = screen.getByLabelText("Working SAR narrative");
    expect(field).toHaveFocus();
    expect(field).toHaveValue("Authoritative working narrative.");
    expect(field).toHaveAttribute("maxlength", "20000");
    await user.clear(field);
    await user.type(field, saved.content);
    expect(screen.getByRole("button", { name: "Reload SAR" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(client.saveSar).toHaveBeenCalledWith("alert-1", {
        content: saved.content,
        expectedCaseVersion: 7,
        expectedSarVersion: 3,
      }),
    );
    expect(onSaved).toHaveBeenCalledWith(saved);
    expect(await screen.findByText(saved.content)).toBeVisible();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(client.decideSubmission).not.toHaveBeenCalled();
  });

  it("cancels typed changes without writing a version", async () => {
    const user = userEvent.setup();
    const client = makeClient({ getAlert: () => Promise.resolve(working()) });
    render(<Editor client={client} />);
    await user.click(await screen.findByRole("button", { name: "Edit SAR" }));
    await user.type(screen.getByRole("textbox"), " Unwanted change.");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Authoritative working narrative.")).toBeVisible();
    expect(client.saveSar).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Reload SAR" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Edit SAR" }));
    expect(screen.getByRole("textbox")).toHaveValue("Authoritative working narrative.");
    await user.clear(screen.getByRole("textbox"));
    await user.type(screen.getByRole("textbox"), "   ");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("prevents typing or cancellation while a save is in flight", async () => {
    const user = userEvent.setup();
    let finish!: (draft: SarDraftView) => void;
    const pending = new Promise<SarDraftView>((resolve) => {
      finish = resolve;
    });
    const client = makeClient({
      getAlert: () => Promise.resolve(working()),
      saveSar: () => pending,
    });
    render(<Editor client={client} />);
    await user.click(await screen.findByRole("button", { name: "Edit SAR" }));
    await user.type(screen.getByRole("textbox"), " Reviewed.");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    finish(sarDraft({ content: "Authoritative working narrative. Reviewed." }));
    expect(await screen.findByRole("button", { name: "Edit SAR" })).toBeEnabled();
  });

  it("retains typed content after a stale or failed save", async () => {
    const user = userEvent.setup();
    const client = makeClient({
      getAlert: () => Promise.resolve(working()),
      saveSar: () => Promise.reject(new Error("case_version_conflict")),
    });
    render(<Editor client={client} />);
    await user.click(await screen.findByRole("button", { name: "Edit SAR" }));
    await user.type(screen.getByRole("textbox"), " Keep this edit.");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    expect(screen.getByRole("textbox")).toHaveValue(
      "Authoritative working narrative. Keep this edit.",
    );
    expect(screen.getByText(/Save or cancel/)).toBeVisible();
  });

  it.each(["submitted", "approved", "rejected", "draft"] as const)(
    "honors blocked editing access for %s reports",
    async (status) => {
      const client = makeClient({
        getAlert: () =>
          Promise.resolve(
            alertDetail({
              alert: alertView({
                capabilities: {
                  ...caseCapabilities([]),
                  edit: { allowed: false, reason: "This report is read only." },
                },
              }),
              sarDraft: sarDraft({ status }),
            }),
          ),
      });
      render(<Editor client={client} />);
      expect(await screen.findByText("This report is read only.")).toBeVisible();
      expect(screen.queryByRole("button", { name: "Edit SAR" })).not.toBeInTheDocument();
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
      expect(client.saveSar).not.toHaveBeenCalled();
    },
  );

  it("takes an unassigned case through the same self-pickup command before editing", async () => {
    const user = userEvent.setup();
    const getAlert = vi.fn().mockResolvedValueOnce(alertDetail()).mockResolvedValue(working());
    const client = makeClient({ getAlert });
    render(<Editor client={client} />);
    await user.click(await screen.findByRole("button", { name: "Take case to edit" }));
    expect(client.actOnAlert).toHaveBeenCalledWith("alert-1", {
      action: "assign",
      expectedCaseVersion: 1,
    });
    expect(await screen.findByRole("button", { name: "Edit SAR" })).toBeEnabled();
  });

  it("keeps streamed evidence when access loading fails and can retry", async () => {
    const user = userEvent.setup();
    const getAlert = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(working());
    render(<Editor client={makeClient({ getAlert })} />);
    expect(await screen.findByText(/The saved SAR could not be refreshed/)).toBeVisible();
    expect(screen.getByText("Streamed evidence retained.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Edit SAR" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reload SAR" }));
    expect(await screen.findByRole("button", { name: "Edit SAR" })).toBeEnabled();
  });
});

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type TransactionResponse } from "../lib/api";
import { signIn, signOut } from "../lib/session";
import { demoPersona, makeClient, transaction } from "../test/factories";
import { Transactions } from "./Transactions";

function login(role: "analyst" | "auditor" | "admin" = "analyst") {
  const persona = demoPersona(role);
  signIn(persona.email, false, role);
}
function clientFor(row: TransactionResponse) {
  return makeClient({
    listTransactions: vi.fn(() =>
      Promise.resolve({ transactions: [row], total: 1, nextCursor: null }),
    ),
  });
}
beforeEach(() => login());
afterEach(() => {
  signOut();
  window.location.hash = "";
});

const saved = () =>
  transaction({
    latestRunId: "saved-run",
    investigation: { runId: "saved-run", status: "completed", errorCode: null },
  });

describe("saved investigations and explicit reruns", () => {
  it.each(["analyst", "auditor"] as const)(
    "opens saved results without POSTs as %s",
    async (role) => {
      signOut();
      login(role);
      const client = clientFor(saved());
      render(<Transactions client={client} />);
      await userEvent.click(await screen.findByRole("button", { name: /View results for/ }));
      expect(window.location.hash).toBe("#/investigations/saved-run");
      expect(client.startInvestigation).not.toHaveBeenCalled();
      if (role === "auditor")
        expect(screen.queryByRole("button", { name: /Re-run/ })).not.toBeInTheDocument();
    },
  );

  it.each(["pending", "running", "retrying"])(
    "opens %s progress and disables rerunning",
    async (status) => {
      const client = clientFor(
        transaction({
          latestRunId: "previous",
          investigation: { runId: "active", status, errorCode: null },
        }),
      );
      render(<Transactions client={client} />);
      expect(await screen.findByRole("button", { name: /Re-run/ })).toBeDisabled();
      await userEvent.click(screen.getByText("TXN-260610-TX1"));
      expect(window.location.hash).toBe("#/investigations/active");
      expect(client.startInvestigation).not.toHaveBeenCalled();
    },
  );

  it("uses latestRunId when no summary was returned", async () => {
    const client = clientFor(transaction({ latestRunId: "pointer" }));
    render(<Transactions client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: /View results for/ }));
    expect(window.location.hash).toBe("#/investigations/pointer");
    expect(client.startInvestigation).not.toHaveBeenCalled();
  });

  it("opens a failed saved run and permits one explicit rerun", async () => {
    const client = clientFor(
      transaction({
        investigation: { runId: "failed", status: "failed", errorCode: "investigation_failed" },
      }),
    );
    render(<Transactions client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: /View failed run/ }));
    expect(client.startInvestigation).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("#/investigations/failed");
    await userEvent.dblClick(screen.getByRole("button", { name: /Re-run/ }));
    expect(client.startInvestigation).toHaveBeenCalledExactlyOnceWith(
      { transactionId: "tx-1", modelOverride: undefined },
      expect.any(String),
    );
    expect(window.location.hash).toBe("#/investigations/run-1");
  });

  it("keeps the same idempotency key after an uncertain network failure", async () => {
    const client = clientFor(saved());
    vi.mocked(client.startInvestigation).mockRejectedValueOnce(new Error("connection lost"));
    render(<Transactions client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: /Re-run/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Re-run/ })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: /Re-run/ }));
    const calls = vi.mocked(client.startInvestigation).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0][1]).toBe(calls[1][1]);
  });

  it("selects an admin override without scoring and sends it only on rerun", async () => {
    signOut();
    login("admin");
    const client = clientFor(saved());
    render(<Transactions client={client} />);
    const selector = await screen.findByLabelText("Model for next investigation");
    await userEvent.selectOptions(selector, "model-v1");
    expect(client.startInvestigation).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /View results for/ }));
    expect(client.startInvestigation).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Re-run/ }));
    expect(client.startInvestigation).toHaveBeenCalledExactlyOnceWith(
      { transactionId: "tx-1", modelOverride: "model-v1" },
      expect.any(String),
    );
  });

  it("navigates to a conflicting active run without another POST", async () => {
    const client = clientFor(saved());
    vi.mocked(client.startInvestigation).mockRejectedValue(
      new ApiError(409, "investigation_in_progress", "Active run", undefined, [
        { field: "runId", message: "existing" },
      ]),
    );
    render(<Transactions client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: /Re-run/ }));
    expect(window.location.hash).toBe("#/investigations/existing");
    expect(client.startInvestigation).toHaveBeenCalledOnce();
  });

  it("starts an uninvestigated row only on explicit user activation", async () => {
    const client = clientFor(transaction());
    render(<Transactions client={client} />);
    const row = await screen.findByText("TXN-260610-TX1");
    expect(client.startInvestigation).not.toHaveBeenCalled();
    await act(async () => {
      await Promise.resolve();
      fireEvent.keyDown(row.closest("tr")!, { key: "Enter" });
    });
    expect(client.startInvestigation).toHaveBeenCalledOnce();
  });
});

it("does not turn an uninvestigated row double-click into two runs", async () => {
  const client = clientFor(transaction());
  render(<Transactions client={client} />);
  await userEvent.dblClick(await screen.findByText("TXN-260610-TX1"));
  expect(client.startInvestigation).toHaveBeenCalledOnce();
});

it("refreshes transaction data without refetching the admin registry or scoring", async () => {
  signOut();
  login("admin");
  const client = clientFor(saved());
  render(<Transactions client={client} />);
  await screen.findByLabelText("Model for next investigation");
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
    await Promise.resolve();
  });
  await waitFor(() => expect(client.listTransactions).toHaveBeenCalledTimes(2));
  expect(client.listModelVersions).toHaveBeenCalledOnce();
  expect(client.startInvestigation).not.toHaveBeenCalled();
});

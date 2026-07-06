import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import { ApiError } from "../lib/api";
import { DEMO_ROLES, signIn, signOut, type UserRole } from "../lib/session";
import { notify } from "../lib/toast";
import { makeClient, transaction } from "../test/factories";
import { Transactions } from "./Transactions";

function signInAs(role: UserRole): void {
  const demoRole = DEMO_ROLES.find((candidate) => candidate.role === role);
  if (!demoRole) {
    throw new Error(`Missing demo role: ${role}`);
  }
  signIn(demoRole.email, false, demoRole.role);
}

beforeEach(() => {
  signInAs("analyst");
});

afterEach(() => {
  window.location.hash = "";
  signOut();
  vi.clearAllMocks();
});

describe("Transactions", () => {
  it("lists transactions and starts an investigation", async () => {
    const client = makeClient();
    render(<Transactions client={client} />);
    expect(await screen.findByText("ext-1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Investigate transaction/ }));
    expect(client.startInvestigation).toHaveBeenCalledWith({
      transactionId: "tx-1",
      modelOverride: undefined,
    });
    await waitFor(() => expect(window.location.hash).toBe("#/investigations/run-1"));
  });

  it("imports a CSV and reloads on success", async () => {
    const client = makeClient();
    render(<Transactions client={client} />);
    await screen.findByText("ext-1");
    const file = new File(["externalId,amount\nx,1"], "txns.csv", { type: "text/csv" });
    Object.defineProperty(file, "text", {
      value: () => Promise.resolve("externalId,amount\nx,1"),
    });
    await userEvent.upload(screen.getByLabelText("Import CSV"), file);
    await waitFor(() => expect(client.uploadCsv).toHaveBeenCalledWith("externalId,amount\nx,1"));
    expect(notify).toHaveBeenCalled();
  });

  it("hides import and investigation actions from auditor sessions", async () => {
    signOut();
    signInAs("auditor");
    const client = makeClient();
    render(<Transactions client={client} />);
    expect(await screen.findByText("ext-1")).toBeInTheDocument();

    expect(screen.queryByLabelText("Import CSV")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Investigate transaction/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("ext-1"));
    expect(client.startInvestigation).not.toHaveBeenCalled();
  });

  it("re-queries the server when the risk-band filter changes", async () => {
    const listTransactions = vi.fn(() =>
      Promise.resolve({ transactions: [transaction()], nextCursor: null, total: 1 }),
    );
    render(<Transactions client={makeClient({ listTransactions })} />);
    await screen.findByText("ext-1");
    await userEvent.click(screen.getByRole("radio", { name: "High" }));
    await waitFor(() =>
      expect(listTransactions).toHaveBeenCalledWith({
        riskBand: "high",
        search: undefined,
        limit: 10,
        cursor: undefined,
      }),
    );
  });

  it("re-queries the server with a debounced search term", async () => {
    const listTransactions = vi.fn(({ search }: { search?: string }) =>
      search === "5555"
        ? Promise.resolve({
            transactions: [transaction({ transactionId: "tx-2", externalId: "other-2" })],
            nextCursor: null,
            total: 1,
          })
        : Promise.resolve({
            transactions: [
              transaction({ transactionId: "tx-1", externalId: "ext-1" }),
              transaction({ transactionId: "tx-2", externalId: "other-2" }),
            ],
            nextCursor: null,
            total: 2,
          }),
    );
    render(<Transactions client={makeClient({ listTransactions })} />);
    expect(await screen.findByText("ext-1")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Search transactions"), "5555");
    // The server is queried with the (debounced) search term, and the results replace the list.
    await waitFor(() =>
      expect(listTransactions).toHaveBeenCalledWith(expect.objectContaining({ search: "5555" })),
    );
    await waitFor(() => expect(screen.queryByText("ext-1")).not.toBeInTheDocument());
    expect(screen.getByText("other-2")).toBeInTheDocument();
  });

  it("pages forward and back with server-side keyset cursors", async () => {
    const rows = Array.from({ length: 12 }, (_, index) =>
      transaction({ transactionId: `tx-${index}`, externalId: `txn-${index}` }),
    );
    const listTransactions = vi.fn(({ cursor }: { cursor?: string }) =>
      cursor
        ? Promise.resolve({ transactions: rows.slice(10), nextCursor: null, total: 12 })
        : Promise.resolve({ transactions: rows.slice(0, 10), nextCursor: "c1", total: 12 }),
    );
    render(<Transactions client={makeClient({ listTransactions })} />);

    // Page 1: first 10 rows, Prev disabled.
    expect(await screen.findByText("txn-0")).toBeInTheDocument();
    expect(screen.getByText("txn-9")).toBeInTheDocument();
    expect(screen.queryByText("txn-10")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1–10 of 12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "← Prev" })).toBeDisabled();

    // Page 2: fetched with the returned cursor; the remaining 2 rows, Next disabled.
    await userEvent.click(screen.getByRole("button", { name: "Next →" }));
    expect(await screen.findByText("txn-10")).toBeInTheDocument();
    expect(screen.getByText("Showing 11–12 of 12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next →" })).toBeDisabled();
    expect(listTransactions).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "c1", limit: 10 }),
    );

    // Back to page 1 (cursor popped).
    await userEvent.click(screen.getByRole("button", { name: "← Prev" }));
    expect(await screen.findByText("txn-0")).toBeInTheDocument();
  });

  it("shows an empty state when there are no transactions", async () => {
    const client = makeClient({
      listTransactions: vi.fn(() =>
        Promise.resolve({ transactions: [], nextCursor: null, total: 0 }),
      ),
    });
    render(<Transactions client={client} />);
    expect(await screen.findByText("No transactions yet")).toBeInTheDocument();
  });

  it("shows an error state when loading fails", async () => {
    const client = makeClient({
      listTransactions: vi.fn(() => Promise.reject(new ApiError(500, "x", "boom"))),
    });
    render(<Transactions client={client} />);
    expect(await screen.findByText("Request failed")).toBeInTheDocument();
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import { ApiError } from "../lib/api";
import { signIn, signOut, type UserRole } from "../lib/session";
import { notify } from "../lib/toast";
import { dashboardMetrics, demoPersona, makeClient, transaction } from "../test/factories";
import { Transactions } from "./Transactions";

const DEFAULT_TRANSACTION_REF = "TXN-260610-TX1";

function signInAs(role: UserRole): void {
  const persona = demoPersona(role);
  signIn(persona.email, false, persona.role);
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
    expect(await screen.findByText(DEFAULT_TRANSACTION_REF)).toBeInTheDocument();
    expect(client.listModelVersions).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Investigate transaction/ }));
    expect(client.startInvestigation).toHaveBeenCalledWith({
      transactionId: "tx-1",
      modelOverride: undefined,
    });
    await waitFor(() => expect(window.location.hash).toBe("#/investigations/run-1"));
  });

  it("shows truthful backend/model provenance and compact account flow", async () => {
    const baseMetrics = dashboardMetrics();
    const client = makeClient({
      getDashboardMetrics: vi.fn(() =>
        Promise.resolve(
          dashboardMetrics({
            modelHealth: {
              ...baseMetrics.modelHealth,
              activeVersionLabel: "xgb-synthetic-ibm-aml-fs2-a1b2c3d4e5",
            },
          }),
        ),
      ),
    });
    render(<Transactions client={client} />);

    const provenance = await screen.findByRole("complementary", { name: "Data provenance" });
    expect(provenance).toHaveTextContent("1 backend-persisted synthetic scenario");
    expect(provenance).toHaveTextContent("IBM AML-trained model v2.0.0");
    expect(screen.getByText("•••• 9876")).toBeInTheDocument();
    expect(screen.getByText("•••• 1234")).toBeInTheDocument();
    expect(screen.getByTitle("Source ID: ext-1")).toHaveTextContent(DEFAULT_TRANSACTION_REF);
    expect(screen.queryByText("ext-1")).not.toBeInTheDocument();
  });

  it("loads the model override selector only for admins", async () => {
    signOut();
    signInAs("admin");
    const client = makeClient();
    render(<Transactions client={client} />);

    expect(await screen.findByText(DEFAULT_TRANSACTION_REF)).toBeInTheDocument();
    expect(client.listModelVersions).toHaveBeenCalledOnce();
  });

  it("imports a CSV and reloads on success", async () => {
    const client = makeClient();
    render(<Transactions client={client} />);
    await screen.findByText(DEFAULT_TRANSACTION_REF);
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
    expect(await screen.findByText(DEFAULT_TRANSACTION_REF)).toBeInTheDocument();

    expect(screen.queryByLabelText("Import CSV")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Investigate transaction/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByText(DEFAULT_TRANSACTION_REF));
    expect(client.startInvestigation).not.toHaveBeenCalled();
  });

  it("re-queries the server when the risk-band filter changes, and mirrors it in the URL", async () => {
    const listTransactions = vi.fn(() =>
      Promise.resolve({ transactions: [transaction()], nextCursor: null, total: 1 }),
    );
    render(<Transactions client={makeClient({ listTransactions })} />);
    await screen.findByText(DEFAULT_TRANSACTION_REF);
    await userEvent.click(screen.getByRole("radio", { name: "High" }));
    await waitFor(() =>
      expect(listTransactions).toHaveBeenCalledWith({
        riskBand: "high",
        search: undefined,
        limit: 10,
        cursor: undefined,
      }),
    );
    // The filtered view is shareable: the dashboard's chips link to exactly this URL.
    expect(window.location.hash).toBe("#/transactions?riskBand=high");
  });

  it("applies a ?riskBand= deep link on entry", async () => {
    window.location.hash = "#/transactions?riskBand=critical";
    const listTransactions = vi.fn(() =>
      Promise.resolve({ transactions: [transaction()], nextCursor: null, total: 1 }),
    );
    render(<Transactions client={makeClient({ listTransactions })} />);
    await screen.findByText(DEFAULT_TRANSACTION_REF);
    expect(listTransactions).toHaveBeenCalledWith(
      expect.objectContaining({ riskBand: "critical" }),
    );
    expect(screen.getByRole("radio", { name: "Critical" })).toBeChecked();
  });

  it("clears the URL filter when the band goes back to All", async () => {
    window.location.hash = "#/transactions?riskBand=high";
    render(<Transactions client={makeClient()} />);
    await screen.findByText(DEFAULT_TRANSACTION_REF);
    await userEvent.click(screen.getByRole("radio", { name: "All" }));
    expect(window.location.hash).toBe("#/transactions");
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
    expect(await screen.findByText(DEFAULT_TRANSACTION_REF)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Search transactions"), "5555");
    // The server is queried with the (debounced) search term, and the results replace the list.
    await waitFor(() =>
      expect(listTransactions).toHaveBeenCalledWith(expect.objectContaining({ search: "5555" })),
    );
    await waitFor(() =>
      expect(screen.queryByText(DEFAULT_TRANSACTION_REF)).not.toBeInTheDocument(),
    );
    expect(screen.getByText("TXN-260610-TX2")).toBeInTheDocument();
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
    expect(await screen.findByText("TXN-260610-TX0")).toBeInTheDocument();
    expect(screen.getByText("TXN-260610-TX9")).toBeInTheDocument();
    expect(screen.queryByText("TXN-260610-TX10")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1–10 of 12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "← Prev" })).toBeDisabled();

    // Page 2: fetched with the returned cursor; the remaining 2 rows, Next disabled.
    await userEvent.click(screen.getByRole("button", { name: "Next →" }));
    expect(await screen.findByText("TXN-260610-TX10")).toBeInTheDocument();
    expect(screen.getByText("Showing 11–12 of 12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next →" })).toBeDisabled();
    expect(listTransactions).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "c1", limit: 10 }),
    );

    // Back to page 1 (cursor popped).
    await userEvent.click(screen.getByRole("button", { name: "← Prev" }));
    expect(await screen.findByText("TXN-260610-TX0")).toBeInTheDocument();
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

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import { ApiError } from "../lib/api";
import { notify } from "../lib/toast";
import { makeClient, transaction } from "../test/factories";
import { Transactions } from "./Transactions";

afterEach(() => {
  window.location.hash = "";
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

  it("re-queries when the risk-band filter changes", async () => {
    const listTransactions = vi.fn(() =>
      Promise.resolve({ transactions: [transaction()], nextCursor: null }),
    );
    render(<Transactions client={makeClient({ listTransactions })} />);
    await screen.findByText("ext-1");
    await userEvent.click(screen.getByRole("radio", { name: "High" }));
    await waitFor(() =>
      expect(listTransactions).toHaveBeenCalledWith({ riskBand: "high", limit: 200 }),
    );
  });

  it("filters loaded rows with the transaction search box", async () => {
    const client = makeClient({
      listTransactions: vi.fn(() =>
        Promise.resolve({
          transactions: [
            transaction({ transactionId: "tx-1", externalId: "ext-1", amount: "12500.00" }),
            transaction({
              transactionId: "tx-2",
              externalId: "other-2",
              amount: "900.00",
              destAccount: "****5555",
            }),
          ],
          nextCursor: null,
        }),
      ),
    });
    render(<Transactions client={client} />);
    expect(await screen.findByText("ext-1")).toBeInTheDocument();
    expect(screen.getByText("other-2")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search transactions"), "5555");
    expect(screen.queryByText("ext-1")).not.toBeInTheDocument();
    expect(screen.getByText("other-2")).toBeInTheDocument();
  });

  it("paginates the loaded window with Prev/Next", async () => {
    const rows = Array.from({ length: 12 }, (_, index) =>
      transaction({ transactionId: `tx-${index}`, externalId: `txn-${index}` }),
    );
    const client = makeClient({
      listTransactions: vi.fn(() => Promise.resolve({ transactions: rows, nextCursor: null })),
    });
    render(<Transactions client={client} />);

    // Page 1: first 10 rows, Prev disabled.
    expect(await screen.findByText("txn-0")).toBeInTheDocument();
    expect(screen.getByText("txn-9")).toBeInTheDocument();
    expect(screen.queryByText("txn-10")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1–10 of 12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "← Prev" })).toBeDisabled();

    // Page 2: the remaining 2 rows, Next disabled.
    await userEvent.click(screen.getByRole("button", { name: "Next →" }));
    expect(await screen.findByText("txn-10")).toBeInTheDocument();
    expect(screen.getByText("txn-11")).toBeInTheDocument();
    expect(screen.queryByText("txn-0")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 11–12 of 12")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next →" })).toBeDisabled();

    // Back to page 1.
    await userEvent.click(screen.getByRole("button", { name: "← Prev" }));
    expect(await screen.findByText("txn-0")).toBeInTheDocument();
  });

  it("shows an empty state when there are no transactions", async () => {
    const client = makeClient({
      listTransactions: vi.fn(() => Promise.resolve({ transactions: [], nextCursor: null })),
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

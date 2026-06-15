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
    await userEvent.click(screen.getByRole("button", { name: "Investigate" }));
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
    await userEvent.upload(screen.getByLabelText("Import transactions (CSV)"), file);
    await waitFor(() => expect(client.uploadCsv).toHaveBeenCalledWith("externalId,amount\nx,1"));
    expect(notify).toHaveBeenCalled();
  });

  it("re-queries when the risk-band filter changes", async () => {
    const listTransactions = vi.fn(() =>
      Promise.resolve({ transactions: [transaction()], nextCursor: null }),
    );
    render(<Transactions client={makeClient({ listTransactions })} />);
    await screen.findByText("ext-1");
    await userEvent.selectOptions(screen.getByLabelText("Filter by risk band"), "high");
    await waitFor(() =>
      expect(listTransactions).toHaveBeenCalledWith({ riskBand: "high", limit: 50 }),
    );
  });

  it("shows an empty state when there are no transactions", async () => {
    const client = makeClient({
      listTransactions: vi.fn(() => Promise.resolve({ transactions: [], nextCursor: null })),
    });
    render(<Transactions client={client} />);
    expect(await screen.findByText("No transactions")).toBeInTheDocument();
  });

  it("shows an error state when loading fails", async () => {
    const client = makeClient({
      listTransactions: vi.fn(() => Promise.reject(new ApiError(500, "x", "boom"))),
    });
    render(<Transactions client={client} />);
    expect(await screen.findByText("Request failed")).toBeInTheDocument();
  });
});

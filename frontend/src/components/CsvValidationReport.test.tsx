import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CsvValidationReport } from "./CsvValidationReport";

const report = { jobId: "job", accepted: 2, duplicates: 1, rejected: 3, sampleErrors: [] };

describe("CsvValidationReport", () => {
  it.each(["ai", "mock"] as const)(
    "explains verified synthetic uploads in %s mode",
    (draftingMode) => {
      render(<CsvValidationReport report={{ ...report, trustedSynthetic: true, draftingMode }} />);
      expect(screen.getByRole("status")).toHaveTextContent("Verified synthetic demo CSV");
      expect(screen.getByRole("status")).toHaveTextContent(
        draftingMode === "ai"
          ? "can use AI drafting after evidence and quality checks"
          : "Providers are disabled",
      );
      expect(screen.getByRole("status")).toHaveTextContent(
        "Human review and approval are required",
      );
      expect(screen.queryByText(/Unverified CSV/)).not.toBeInTheDocument();
    },
  );

  it("shows counts, physical row numbers, bounded samples and the local restriction", () => {
    render(
      <CsvValidationReport
        report={{
          ...report,
          sampleErrors: [
            {
              index: 2,
              rowNumber: 5,
              externalId: null,
              code: "validation_error",
              reasonCode: "precision_exceeded",
              message: "amount: precision_exceeded",
            },
          ],
        }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("2 accepted · 1 duplicate · 3 rejected");
    expect(screen.getByRole("list", { name: "Validation rejection samples" })).toHaveTextContent(
      "Row 5: precision_exceeded — amount: precision_exceeded",
    );
    expect(screen.getByText(/external AI drafting stays disabled/)).toBeInTheDocument();
    expect(screen.getByText(/Only a bounded sample/)).toBeInTheDocument();
  });

  it("does not show an empty rejection list for valid rows", () => {
    render(<CsvValidationReport report={{ ...report, rejected: 0 }} />);
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.queryByText(/Only a bounded sample/)).not.toBeInTheDocument();
  });
});

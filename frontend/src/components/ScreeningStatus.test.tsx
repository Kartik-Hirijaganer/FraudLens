import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { ScreeningStatus } from "./ScreeningStatus";

it("keeps an empty queue quiet", () => {
  render(<ScreeningStatus progress={null} error={false} onRetry={vi.fn()} />);
  expect(screen.queryByLabelText("Screening progress")).not.toBeInTheDocument();
});

it("distinguishes completed scoring, core failures, recovery and blocked drafting", async () => {
  const retry = vi.fn();
  render(
    <ScreeningStatus
      progress={{
        total: 8,
        completed: 3,
        failed: 1,
        pending: 2,
        running: 1,
        retrying: 1,
        draftingBlocked: 2,
        pollAfterMs: 100,
      }}
      error
      onRetry={retry}
    />,
  );
  const status = screen.getByLabelText("Screening progress");
  expect(status).toHaveAttribute("aria-live", "polite");
  expect(status).toHaveTextContent("4 of 8 screened");
  expect(status).toHaveTextContent("2 queued · 1 running · 1 retrying");
  expect(status).toHaveTextContent("1 failed");
  expect(status).toHaveTextContent("2 drafting blocked");
  await userEvent.click(screen.getByRole("button", { name: "Retry updates" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("allows observation retries even before the first progress response", () => {
  render(<ScreeningStatus progress={null} error onRetry={vi.fn()} />);
  expect(screen.getByText(/Progress updates interrupted/)).toBeInTheDocument();
});

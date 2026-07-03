import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("renders shown of total copy", () => {
    render(<Pagination shown={10} total={25} />);
    expect(screen.getByText("Showing 10 of 25")).toBeInTheDocument();
  });

  it("renders load-more only when more data is available", async () => {
    const onMore = vi.fn();
    render(<Pagination shown={10} total={25} hasMore onMore={onMore} />);
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(onMore).toHaveBeenCalled();
  });

  it("renders a range and drives Prev/Next when handlers are supplied", async () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    render(
      <Pagination
        total={8142}
        rangeStart={11}
        rangeEnd={20}
        hasPrev
        hasNext
        onPrev={onPrev}
        onNext={onNext}
      />,
    );
    expect(screen.getByText("Showing 11–20 of 8,142")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "← Prev" }));
    await userEvent.click(screen.getByRole("button", { name: "Next →" }));
    expect(onPrev).toHaveBeenCalled();
    expect(onNext).toHaveBeenCalled();
  });

  it("disables Prev/Next at the ends of the range", () => {
    render(<Pagination total={5} rangeStart={1} rangeEnd={5} onPrev={vi.fn()} onNext={vi.fn()} />);
    expect(screen.getByRole("button", { name: "← Prev" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next →" })).toBeDisabled();
  });
});

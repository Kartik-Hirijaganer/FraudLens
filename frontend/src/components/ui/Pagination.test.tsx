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
});

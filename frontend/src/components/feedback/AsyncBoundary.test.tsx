import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api";
import type { AsyncState } from "../../lib/useAsync";
import { AsyncBoundary } from "./AsyncBoundary";

function makeState<T>(overrides: Partial<AsyncState<T>>): AsyncState<T> {
  return { data: null, loading: false, error: null, reload: vi.fn(), ...overrides };
}

describe("AsyncBoundary", () => {
  it("shows the default skeleton while first-loading", () => {
    const { container } = render(
      <AsyncBoundary state={makeState<string>({ loading: true })}>
        {(value) => <span>{value}</span>}
      </AsyncBoundary>,
    );
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });

  it("shows a custom skeleton when provided", () => {
    render(
      <AsyncBoundary state={makeState<string>({ loading: true })} skeleton={<p>loading…</p>}>
        {(value) => <span>{value}</span>}
      </AsyncBoundary>,
    );
    expect(screen.getByText("loading…")).toBeInTheDocument();
  });

  it("shows an error + retry that calls reload", async () => {
    const reload = vi.fn();
    render(
      <AsyncBoundary
        state={makeState<string>({
          error: new ApiError(409, "duplicate_external_id", "dup"),
          reload,
        })}
      >
        {(value) => <span>{value}</span>}
      </AsyncBoundary>,
    );
    expect(screen.getByText("Already ingested")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("renders the children with data", () => {
    render(
      <AsyncBoundary state={makeState<string>({ data: "ready" })}>
        {(value) => <span>{value}</span>}
      </AsyncBoundary>,
    );
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("renders nothing when idle with no data", () => {
    const { container } = render(
      <AsyncBoundary state={makeState<string>({})}>
        {(value) => <span>{value}</span>}
      </AsyncBoundary>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./toast", () => ({ notify: vi.fn(), notifyError: vi.fn() }));

import { notify, notifyError } from "./toast";
import { useAsyncAction } from "./useAsyncAction";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useAsyncAction", () => {
  it("runs the action, toasts success, and calls onSuccess", async () => {
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useAsyncAction(onSuccess));
    await act(async () => {
      await result.current.run(() => Promise.resolve(), "Done");
    });
    expect(notify).toHaveBeenCalledWith({ tone: "positive", title: "Done" });
    expect(onSuccess).toHaveBeenCalledOnce();
    expect(result.current.busy).toBe(false);
  });

  it("skips the success toast when no title is given", async () => {
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useAsyncAction(onSuccess));
    await act(async () => {
      await result.current.run(() => Promise.resolve());
    });
    expect(notify).not.toHaveBeenCalled();
    expect(onSuccess).toHaveBeenCalledOnce();
  });

  it("notifies the error and skips onSuccess on failure", async () => {
    const onSuccess = vi.fn();
    const { result } = renderHook(() => useAsyncAction(onSuccess));
    await act(async () => {
      await result.current.run(() => Promise.reject(new Error("nope")), "Done");
    });
    expect(notifyError).toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
  });
});

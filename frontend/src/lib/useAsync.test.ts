import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useAsync } from "./useAsync";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useAsync", () => {
  it("resolves to data and clears loading", async () => {
    const fn = vi.fn(() => Promise.resolve(42));
    const { result } = renderHook(() => useAsync(fn, []));
    expect(result.current.loading).toBe(true);
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.data).toBe(42);
    expect(result.current.loading).toBe(false);
  });

  it("captures an error", async () => {
    const fn = vi.fn(() => Promise.reject(new Error("boom")));
    const { result } = renderHook(() => useAsync(fn, []));
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.loading).toBe(false);
  });

  it("re-runs the function on reload", async () => {
    const fn = vi.fn(() => Promise.resolve(1));
    const { result } = renderHook(() => useAsync(fn, []));
    await act(async () => {
      await Promise.resolve();
    });
    expect(fn).toHaveBeenCalledTimes(1);
    act(() => result.current.reload());
    await act(async () => {
      await Promise.resolve();
    });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("ignores a resolution after unmount", async () => {
    const success = deferred<number>();
    const { unmount } = renderHook(() => useAsync(() => success.promise, []));
    unmount();
    await act(async () => {
      success.resolve(7);
      await success.promise;
    });
  });

  it("ignores a rejection after unmount", async () => {
    const failure = deferred<number>();
    const { unmount } = renderHook(() => useAsync(() => failure.promise, []));
    unmount();
    await act(async () => {
      failure.reject(new Error("late"));
      await failure.promise.catch(() => undefined);
    });
  });
});

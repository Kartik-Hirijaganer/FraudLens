import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { ScreeningProgress } from "./api";
import { makeClient } from "../test/factories";
import { useScreeningRefresh } from "./useScreeningRefresh";

const progress = (overrides: Partial<ScreeningProgress> = {}): ScreeningProgress => ({
  total: 1,
  pending: 1,
  running: 0,
  retrying: 0,
  completed: 0,
  failed: 0,
  draftingBlocked: 0,
  pollAfterMs: 100,
  ...overrides,
});
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it("polls persisted progress without overlap, refreshes at completion, and then stops", async () => {
  const client = makeClient({
    getScreeningProgress: vi
      .fn()
      .mockResolvedValueOnce(progress())
      .mockResolvedValue(progress({ pending: 0, completed: 1 })),
  });
  const refresh = vi.fn();
  const { result, unmount } = renderHook(() => useScreeningRefresh(client, refresh));
  await act(async () => {});
  expect(refresh).toHaveBeenCalledTimes(1);
  await act(async () => {
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(100);
  });
  expect(result.current.progress?.completed).toBe(1);
  expect(refresh).toHaveBeenCalledTimes(2);
  await act(async () => {
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(client.getScreeningProgress).toHaveBeenCalledTimes(2);
  unmount();
});

it("coalesces focus checks, retains saved data on failure, and recovers on retry", async () => {
  let resolve: (value: ScreeningProgress) => void = () => undefined;
  const client = makeClient({
    getScreeningProgress: vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<ScreeningProgress>((done) => {
            resolve = done;
          }),
      )
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(progress({ pending: 0, failed: 1 })),
  });
  const { result, unmount } = renderHook(() => useScreeningRefresh(client, vi.fn()));
  act(() => {
    window.dispatchEvent(new Event("focus"));
    result.current.refresh();
  });
  expect(client.getScreeningProgress).toHaveBeenCalledTimes(1);
  await act(async () => {
    await Promise.resolve();
    resolve(progress());
  });
  expect(result.current.error).toBe(true);
  expect(result.current.progress?.total).toBe(1);
  await act(async () => {
    await Promise.resolve();
    result.current.refresh();
  });
  expect(result.current.error).toBe(false);
  expect(result.current.progress?.failed).toBe(1);
  unmount();
  act(() => {
    window.dispatchEvent(new Event("focus"));
  });
  expect(client.getScreeningProgress).toHaveBeenCalledTimes(3);
});

it("ignores a late resolution after unmount and uses the latest refresh callback", async () => {
  let resolve: (value: ScreeningProgress) => void = () => undefined;
  const client = makeClient({
    getScreeningProgress: vi.fn(
      () =>
        new Promise<ScreeningProgress>((done) => {
          resolve = done;
        }),
    ),
  });
  const original = vi.fn();
  const latest = vi.fn();
  const { rerender, unmount } = renderHook(
    ({ callback }) => useScreeningRefresh(client, callback),
    { initialProps: { callback: original } },
  );
  rerender({ callback: latest });
  await act(async () => {
    await Promise.resolve();
    resolve(progress());
  });
  expect(original).not.toHaveBeenCalled();
  expect(latest).toHaveBeenCalledOnce();
  act(() => {
    window.dispatchEvent(new Event("focus"));
  });
  unmount();
  await act(async () => {
    await Promise.resolve();
    resolve(progress());
  });
  expect(latest).toHaveBeenCalledOnce();
});

it("an empty queue is read-only and rechecked on focus", async () => {
  const client = makeClient();
  const refresh = vi.fn();
  renderHook(() => useScreeningRefresh(client, refresh));
  await act(async () => {});
  expect(refresh).not.toHaveBeenCalled();
  expect(client.startInvestigation).not.toHaveBeenCalled();
  await act(async () => {
    await Promise.resolve();
    window.dispatchEvent(new Event("focus"));
  });
  expect(client.getScreeningProgress).toHaveBeenCalledTimes(2);
  expect(refresh).toHaveBeenCalledOnce();
});

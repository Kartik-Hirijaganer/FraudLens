import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usePrefersReducedMotion } from "./motion";

type ChangeHandler = (event: MediaQueryListEvent) => void;

function installMatchMedia(initial: boolean) {
  const handlers = new Set<ChangeHandler>();
  const mql = {
    matches: initial,
    addEventListener: (_type: string, cb: ChangeHandler) => handlers.add(cb),
    removeEventListener: (_type: string, cb: ChangeHandler) => handlers.delete(cb),
  };
  window.matchMedia = vi.fn(() => mql) as unknown as typeof window.matchMedia;
  return (matches: boolean): void =>
    handlers.forEach((cb) => cb({ matches } as MediaQueryListEvent));
}

afterEach(() => {
  (window as { matchMedia?: unknown }).matchMedia = undefined;
});

describe("usePrefersReducedMotion", () => {
  it("reports the current preference and updates on change", () => {
    const emit = installMatchMedia(true);
    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(true);
    act(() => emit(false));
    expect(result.current).toBe(false);
  });

  it("defaults to false when matchMedia is unavailable", () => {
    (window as { matchMedia?: unknown }).matchMedia = undefined;
    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(false);
  });
});

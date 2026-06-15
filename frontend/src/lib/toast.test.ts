import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(() => "id-success"),
    warning: vi.fn(() => "id-warning"),
    error: vi.fn(() => "id-error"),
    message: vi.fn(() => "id-message"),
  },
}));

import { toast } from "sonner";

import { DEFAULT_TOAST_DURATION_MS, notify, notifyError } from "./toast";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("notify", () => {
  it("routes each tone to the matching Sonner variant with the default duration", () => {
    notify({ tone: "positive", title: "Saved" });
    expect(toast.success).toHaveBeenCalledWith("Saved", {
      description: undefined,
      duration: DEFAULT_TOAST_DURATION_MS,
    });
    notify({ tone: "warning", title: "Careful" });
    expect(toast.warning).toHaveBeenCalled();
  });

  it("honors a custom timeout for a neutral toast", () => {
    notify({ title: "Heads up", durationMs: 1234 });
    expect(toast.message).toHaveBeenCalledWith("Heads up", {
      description: undefined,
      duration: 1234,
    });
  });

  it("makes critical toasts persist (Infinity duration)", () => {
    notify({ tone: "negative", title: "Stop", critical: true });
    expect(toast.error).toHaveBeenCalledWith("Stop", {
      description: undefined,
      duration: Infinity,
    });
  });
});

describe("notifyError", () => {
  it("shows a critical error toast for a flagged API error", () => {
    notifyError(new ApiError(503, "investigations_unavailable", "x"));
    expect(toast.error).toHaveBeenCalledWith(
      "Investigations unavailable",
      expect.objectContaining({ duration: Infinity }),
    );
  });
});

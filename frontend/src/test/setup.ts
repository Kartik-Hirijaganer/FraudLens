/**
 * Summary: Shared Vitest browser setup for jest-dom and deterministic Web Storage.
 *
 * Key classes:
 * - TestStorage: Storage-compatible memory implementation for Node builds without localStorage.
 *
 * Key functions:
 * - installStorage: restore a missing jsdom storage surface before each test module executes.
 *
 * Notes:
 * - Real browser storage is never replaced; the fallback only covers Node's undefined experimental
 *   global, which otherwise makes session tests depend on worker initialization order.
 */
import "@testing-library/jest-dom/vitest";

class TestStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(String(key), String(value));
  }
}

function installStorage(name: "localStorage" | "sessionStorage"): void {
  if (window[name] !== undefined) return;
  Object.defineProperty(window, name, { configurable: true, value: new TestStorage() });
}

const needsStorageFallback =
  window.localStorage === undefined || window.sessionStorage === undefined;
if (globalThis.Storage === undefined || needsStorageFallback) {
  Object.defineProperty(globalThis, "Storage", { configurable: true, value: TestStorage });
}
installStorage("localStorage");
installStorage("sessionStorage");

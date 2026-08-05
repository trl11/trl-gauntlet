import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// The kit's Tooltip starts a timer when the pointer enters it and clears it
// only when the pointer leaves, so a test that clicks something wearing one
// can leave a timer behind. It then fires after jsdom has been torn down and
// fails whichever file happened to be last, so every timer a test starts is
// noted and cleared when it ends.
const startTimer = globalThis.setTimeout;
const clearTimer = globalThis.clearTimeout;
const pending = new Set<number>();

globalThis.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
  const id = startTimer(handler as () => void, timeout, ...args);
  pending.add(id);
  return id;
}) as typeof globalThis.setTimeout;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  for (const id of pending) clearTimer(id);
  pending.clear();
});

// jsdom implements neither of these, and the ui-kit and recharts both read them.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
}

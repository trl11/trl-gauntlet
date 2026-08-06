import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RunRow } from "@api/types";

import ActiveRun from "./ActiveRun";

vi.mock("@api/client", () => ({
  abortRun: vi.fn(),
  runEventsUrl: (runId: string) => `/api/runs/${runId}/events`,
  stopRun: vi.fn(),
}));

/** Minimal EventSource stand-in; jsdom ships none. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;

  private readonly listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  close(): void {}

  emit(type: string, payload: Record<string, unknown>): void {
    const event = { data: JSON.stringify({ type, ...payload }) } as MessageEvent<string>;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

const RUN: RunRow = {
  duration_s: null,
  ended_at: null,
  fail_reason: "",
  profile: "quick.yaml",
  run_dir: "/runs/thermal_cycle/r1",
  run_id: "r1",
  started_at: "2026-01-01T00:00:00Z",
  status: "running",
  suite: "thermal_cycle",
  target: null,
  unit_serial: "HC-001",
  verdict: null,
};

function renderRun() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ActiveRun now={Date.parse("2026-01-01T00:00:30Z")} run={RUN} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** The stream this run opened, which the hook holds for as long as it is mounted. */
function stream(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (!source) throw new Error("no EventSource was opened");
  return source;
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

describe("ActiveRun", () => {
  it("shows the elapsed time and the two ways to end the run", () => {
    renderRun();
    expect(screen.getByText("30s")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Abort" })).toBeInTheDocument();
  });

  it("opens the run details", () => {
    renderRun();
    expect(screen.getByRole("link", { name: "thermal_cycle" })).toHaveAttribute("href", "/runs/r1");
  });

  it("draws no progress until the run has reported some", () => {
    renderRun();
    expect(screen.queryByRole("region", { name: "Iterations" })).not.toBeInTheDocument();
  });

  it("burns one square per iteration as the run reports them", () => {
    renderRun();

    act(() => {
      stream().emit("iteration", {
        elapsed_run_s: 2,
        images: [],
        iteration: 1,
        reason: "",
        seq: 0,
        success: true,
        ts: 1767225600,
      });
      stream().emit("iteration", {
        elapsed_run_s: 4,
        images: [],
        iteration: 2,
        reason: "chamber busy, skipped",
        seq: 1,
        success: true,
        ts: 1767225602,
      });
      stream().emit("iteration", {
        elapsed_run_s: 6,
        images: [],
        iteration: 3,
        reason: "rail low",
        seq: 2,
        success: false,
        ts: 1767225604,
      });
    });

    expect(screen.getByText("3 iterations, 1 failed, 1 warned")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "#1 · passed · 2s" })).toHaveClass(
      "iteration-map__cell--ok"
    );
    expect(
      screen.getByRole("button", { name: "#2 · passed with warnings · 2s · chamber busy, skipped" })
    ).toHaveClass("iteration-map__cell--warned");
    expect(screen.getByRole("button", { name: "#3 · failed · 2s · rail low" })).toHaveClass(
      "iteration-map__cell--failed"
    );
  });

  it("counts the iteration in flight from the phases it has reported", () => {
    renderRun();

    act(() => {
      stream().emit("phase", {
        detail: {},
        elapsed_s: 1,
        iteration: 1,
        phase: "soak",
        seq: 0,
        success: true,
        ts: 1767225600,
      });
    });

    expect(screen.getByRole("button", { name: "#1 · passed · 1s" })).toBeInTheDocument();
  });
});

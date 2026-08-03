import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useEventStream } from "./useEventStream";

/** Minimal EventSource stand-in; jsdom ships none. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readonly url: string;
  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;

  private readonly listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }

  open(): void {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }

  emit(type: string, payload: Record<string, unknown>): void {
    const event = { data: JSON.stringify({ type, ...payload }) } as MessageEvent<string>;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }

  fail(): void {
    this.readyState = FakeEventSource.CLOSED;
    this.onerror?.();
  }
}

function latest(): FakeEventSource {
  const instance = FakeEventSource.instances.at(-1);
  if (!instance) throw new Error("no EventSource was opened");
  return instance;
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useEventStream", () => {
  it("opens nothing without a run id", () => {
    renderHook(() => useEventStream({ runId: null }));
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("subscribes from the start of the stream", () => {
    renderHook(() => useEventStream({ runId: "r1" }));
    expect(latest().url).toBe("/api/runs/r1/events?since=0");
  });

  it("reports the connection opening", async () => {
    const { result } = renderHook(() => useEventStream({ runId: "r1" }));
    act(() => latest().open());
    await waitFor(() => expect(result.current.connected).toBe(true));
  });

  it("accumulates every event type", async () => {
    const { result } = renderHook(() => useEventStream({ runId: "r1" }));
    const source = latest();

    act(() => {
      source.open();
      source.emit("status", { seq: 1, ts: 1, status: "running" });
      source.emit("log", { seq: 2, ts: 2, level: "info", message: "hello" });
      source.emit("metrics", { seq: 3, ts: 3, iteration: 1, elapsed_s: 1, values: { "a.b": 2 } });
      source.emit("phase", {
        seq: 4,
        ts: 4,
        iteration: 1,
        phase: "soak",
        elapsed_s: 1,
        success: true,
      });
      source.emit("iteration", { seq: 5, ts: 5, iteration: 1, success: true, images: [] });
      source.emit("anomaly", { seq: 6, ts: 6, probe: "rail", anomaly_kind: "spike" });
      source.emit("verdict", { seq: 7, ts: 7, result: "PASS", reason: "", summary: {} });
    });

    await waitFor(() => expect(result.current.verdict?.result).toBe("PASS"));
    expect(result.current.status).toBe("running");
    expect(result.current.logs).toHaveLength(1);
    expect(result.current.logs[0].message).toBe("hello");
    expect(result.current.metrics).toHaveLength(1);
    expect(result.current.metricNames).toEqual(["a.b"]);
    expect(result.current.phases).toHaveLength(1);
    expect(result.current.iterations).toHaveLength(1);
    expect(result.current.anomalies).toHaveLength(1);
    expect(result.current.ended).toBe(false);
  });

  it("caps the log buffer at the newest lines", async () => {
    const { result } = renderHook(() => useEventStream({ runId: "r1", maxLogLines: 2 }));
    const source = latest();
    act(() => {
      source.open();
      for (let seq = 1; seq <= 4; seq += 1) {
        source.emit("log", { seq, ts: seq, level: "info", message: `line ${seq}` });
      }
    });
    await waitFor(() => expect(result.current.logs).toHaveLength(2));
    expect(result.current.logs.map((line) => line.message)).toEqual(["line 3", "line 4"]);
  });

  it("ignores malformed frames", async () => {
    const { result } = renderHook(() => useEventStream({ runId: "r1" }));
    const source = latest();
    act(() => {
      source.open();
      source.onmessage?.({ data: "{ not json" } as MessageEvent<string>);
    });
    await waitFor(() => expect(result.current.logs).toHaveLength(0));
  });

  it("stops for good once the run ends", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useEventStream({ runId: "r1" }));
    const source = latest();
    act(() => {
      source.open();
      source.emit("end", { run_id: "r1" });
    });
    expect(result.current.ended).toBe(true);
    act(() => {
      source.fail();
      vi.advanceTimersByTime(30_000);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("reconnects with backoff, resuming after the last sequence", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useEventStream({ runId: "r1" }));
    const first = latest();
    act(() => {
      first.open();
      first.emit("log", { seq: 4, ts: 4, level: "info", message: "before the drop" });
      first.fail();
    });
    expect(result.current.error).toContain("reconnecting");

    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(latest().url).toBe("/api/runs/r1/events?since=4");

    act(() => {
      latest().fail();
      vi.advanceTimersByTime(999);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(FakeEventSource.instances).toHaveLength(3);
  });

  it("closes the stream on unmount", () => {
    const { unmount } = renderHook(() => useEventStream({ runId: "r1" }));
    const source = latest();
    act(() => source.open());
    unmount();
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });
});

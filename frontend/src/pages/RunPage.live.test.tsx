/**
 * A live run and a finished one must look the same.
 *
 * The run view feeds one set of components from two sources: the SSE stream
 * while a run is in flight, and `metrics.jsonl` plus `test.log` once it has
 * finished. `utils/run_history.replay` exists to make those two produce the
 * same rows, so the test renders the same run both ways and compares the
 * markup the panels produce.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getArtifactText,
  getRun,
  getRunManifest,
  getRunMetrics,
  getRunVerdict,
  listArtifacts,
  listRunNotes,
} from "@api/client";
import type { RunRow } from "@api/types";

import RunPage from "./RunPage";

vi.mock("@api/client", () => ({
  abortRun: vi.fn(),
  addRunNote: vi.fn(),
  artifactUrl: (runId: string, path: string) => `/api/runs/${runId}/artifacts/${path}`,
  deleteRunNote: vi.fn(),
  getArtifactText: vi.fn(),
  getRun: vi.fn(),
  getRunManifest: vi.fn(),
  getRunMetrics: vi.fn(),
  getRunVerdict: vi.fn(),
  listArtifacts: vi.fn(),
  listRunNotes: vi.fn(),
  runEventsUrl: (runId: string) => `/api/runs/${runId}/events`,
  stopRun: vi.fn(),
}));

/** Minimal EventSource stand-in; jsdom ships none. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onopen: (() => void) | null = null;
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

  close(): void {}

  emit(type: string, payload: Record<string, unknown>): void {
    const event = { data: JSON.stringify({ type, ...payload }) } as MessageEvent<string>;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

const RUN: RunRow = {
  duration_s: 6,
  ended_at: "2026-01-01T00:00:06Z",
  fail_reason: "",
  profile: "mock.yaml",
  run_dir: "/runs/run-1",
  run_id: "run-1",
  started_at: "2026-01-01T00:00:00Z",
  status: "failed",
  suite: "thermal_cycle",
  target: null,
  unit_serial: "SN-42",
  verdict: "FAIL",
};

/** `metrics.jsonl` as the artifact endpoint returns it. */
const RECORDS = [
  {
    elapsed_run_s: 2,
    iteration: 1,
    kind: "iteration",
    metrics: { rail: { volts: 3.3 } },
    phases: [{ detail: {}, elapsed_s: 2, error: null, name: "soak", success: true }],
    success: true,
    timestamp: 1767225600,
  },
  {
    elapsed_run_s: 5,
    iteration: 2,
    kind: "iteration",
    metrics: { rail: { volts: 2.9 } },
    reason: "rail low",
    success: false,
    timestamp: 1767225603,
  },
  {
    anomaly_kind: "out_of_envelope",
    detail: { volts: 2.9 },
    kind: "anomaly",
    probe: "rail",
    timestamp: 1767225603,
  },
];

const LOG_TEXT = "boot ok\nERROR rail low";

/** `verdict.json`, and the same object inside the live verdict event. */
const VERDICT = {
  failures: 1,
  passed: false,
  reason: "rail low",
  successes: 1,
  total_iterations: 2,
};

/**
 * The same run as it arrives over SSE.
 *
 * `replay` numbers replayed rows by their index in `metrics.jsonl`, so the
 * sequence numbers here are those indices.
 */
function emitTheRun(source: FakeEventSource): void {
  source.emit("log", { seq: 0, ts: 1767225600, level: "info", message: "boot ok" });
  source.emit("log", { seq: 1, ts: 1767225603, level: "error", message: "ERROR rail low" });
  source.emit("metrics", {
    seq: 0,
    ts: 1767225600,
    elapsed_s: 2,
    iteration: 1,
    values: { "rail.volts": 3.3 },
  });
  source.emit("phase", {
    seq: 0,
    ts: 1767225600,
    detail: {},
    elapsed_s: 2,
    iteration: 1,
    phase: "soak",
    success: true,
  });
  source.emit("iteration", {
    seq: 0,
    ts: 1767225600,
    elapsed_run_s: 2,
    images: [],
    iteration: 1,
    reason: "",
    success: true,
  });
  source.emit("metrics", {
    seq: 1,
    ts: 1767225603,
    elapsed_s: 5,
    iteration: 2,
    values: { "rail.volts": 2.9 },
  });
  source.emit("iteration", {
    seq: 1,
    ts: 1767225603,
    elapsed_run_s: 5,
    images: [],
    iteration: 2,
    reason: "rail low",
    success: false,
  });
  source.emit("anomaly", {
    seq: 2,
    ts: 1767225603,
    anomaly_kind: "out_of_envelope",
    detail: { volts: 2.9 },
    probe: "rail",
  });
  source.emit("verdict", {
    seq: 3,
    ts: 1767225603,
    result: "FAIL",
    reason: "rail low",
    summary: VERDICT,
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/runs/run-1"]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/**
 * Blank out the ids React's `useId` hands to form controls.
 *
 * They count renders rather than describing content, so the same markup gets
 * different ones on a second render and would defeat the comparison.
 */
function withoutGeneratedIds(html: string): string {
  return html.replace(/_r_[0-9a-z]+_/g, "_id_");
}

/** Everything one render of one tab put on the page. */
interface Capture {
  anomalies: string;
  iterationMap: string;
  logLines: string[];
  logTimes: string[];
  panel: string;
  provenance: boolean;
  series: number;
  verdict: string;
}

/** Wait until the events, from whichever source, have reached the page. */
async function settle(): Promise<void> {
  await screen.findByRole("region", { name: "Anomalies" });
}

/** Open one tab and read back what it drew, before the page goes away. */
async function capture(tab: string): Promise<Capture> {
  await userEvent.click(screen.getByRole("tab", { name: tab }));
  const rows = document.querySelectorAll(".log-stream__row");
  return {
    anomalies: withoutGeneratedIds(screen.getByRole("region", { name: "Anomalies" }).innerHTML),
    iterationMap: screen.queryByRole("region", { name: "Iterations" })?.innerHTML ?? "",
    logLines: [...rows].map(
      (row) =>
        `${row.querySelector(".log-stream__level")?.textContent} ` +
        `${row.querySelector(".log-stream__message")?.textContent}`
    ),
    logTimes: [...rows].map((row) => row.querySelector(".log-stream__time")?.textContent ?? ""),
    panel: withoutGeneratedIds(screen.getByRole("tabpanel").innerHTML),
    provenance: screen.queryByText("Provenance") !== null,
    series: screen.queryAllByLabelText("rail.volts").length,
    verdict: screen.queryByRole("region", { name: "Verdict" })?.innerHTML ?? "",
  };
}

/** Render the finished run, served from its stored artifacts. */
async function fromHistory(tab: string): Promise<Capture> {
  vi.mocked(getRun).mockResolvedValue(RUN);
  const view = renderPage();
  await settle();
  const captured = await capture(tab);
  view.unmount();
  return captured;
}

/** Render the same run live, served from the event stream. */
async function fromStream(tab: string): Promise<Capture> {
  vi.mocked(getRun).mockResolvedValue({ ...RUN, ended_at: null, status: "running" });
  const view = renderPage();
  await screen.findByRole("heading", { name: "thermal_cycle" });

  const source = FakeEventSource.instances.at(-1);
  if (!source) throw new Error("no EventSource was opened");
  act(() => emitTheRun(source));
  await settle();

  const captured = await capture(tab);
  view.unmount();
  return captured;
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.mocked(getRunMetrics).mockResolvedValue({
    count: RECORDS.length,
    records: RECORDS as never,
    run_id: "run-1",
  });
  vi.mocked(getRunVerdict).mockResolvedValue({
    passed: false,
    reason: "rail low",
    successes: 1,
    failures: 1,
    total_iterations: 2,
  } as never);
  vi.mocked(getRunManifest).mockResolvedValue({ hostname: "bench-1" } as never);
  vi.mocked(listArtifacts).mockResolvedValue({
    artifacts: [{ path: "test.log", size: 40, text: true }],
    run_dir: "/runs/run-1",
    run_id: "run-1",
  });
  vi.mocked(getArtifactText).mockResolvedValue(LOG_TEXT);
  vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("RunPage renders a run the same live and from history", () => {
  it("draws the same iterations", async () => {
    const live = await fromStream("iterations");
    const stored = await fromHistory("iterations");
    expect(live.panel).toContain("rail low");
    expect(live.panel).toBe(stored.panel);
  });

  it("draws the same anomalies", async () => {
    const live = await fromStream("iterations");
    const stored = await fromHistory("iterations");
    expect(live.anomalies).toContain("out_of_envelope");
    expect(live.anomalies).toBe(stored.anomalies);
  });

  it("draws the same verdict", async () => {
    const live = await fromStream("overview");
    const stored = await fromHistory("overview");
    expect(live.verdict).toContain("FAILED");
    expect(live.verdict).toBe(stored.verdict);
  });

  it("draws the same iteration map", async () => {
    const live = await fromStream("overview");
    const stored = await fromHistory("overview");
    expect(live.iterationMap).toContain("#1 · passed");
    expect(live.iterationMap).toBe(stored.iterationMap);
  });

  it("names the host only once the run has finished", async () => {
    // manifest.json is written when the suite process ends, so a live run has
    // no provenance to show. It is the one part of the overview that differs.
    const live = await fromStream("overview");
    const stored = await fromHistory("overview");
    expect(live.provenance).toBe(false);
    expect(stored.provenance).toBe(true);
  });

  it("draws the same log lines", async () => {
    const live = await fromStream("log");
    const stored = await fromHistory("log");
    expect(live.logLines).toEqual(["info boot ok", "error ERROR rail low"]);
    expect(live.logLines).toEqual(stored.logLines);
  });

  it("times the live log lines, because test.log carries no timestamps", async () => {
    const live = await fromStream("log");
    const stored = await fromHistory("log");
    expect(live.logTimes).toEqual(["00:00:00", "00:00:03"]);
    expect(stored.logTimes).toEqual(["", ""]);
  });

  it("discovers the same metric series", async () => {
    const live = await fromStream("metrics");
    const stored = await fromHistory("metrics");
    expect(live.series).toBe(1);
    expect(live.series).toBe(stored.series);
  });

  it("reads nothing from the stored artifacts while a run is live", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...RUN, ended_at: null, status: "running" });
    renderPage();
    await screen.findByRole("heading", { name: "thermal_cycle" });
    expect(getRunMetrics).not.toHaveBeenCalled();
    expect(getRunVerdict).not.toHaveBeenCalled();
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("opens no stream for a run that has already finished", async () => {
    vi.mocked(getRun).mockResolvedValue(RUN);
    renderPage();
    await screen.findByRole("heading", { name: "thermal_cycle" });
    await waitFor(() => expect(getRunMetrics).toHaveBeenCalled());
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});

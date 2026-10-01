import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  abortRun,
  addRunNote,
  deleteRunNote,
  getArtifactText,
  getDaqRecording,
  getDaqWindow,
  getRun,
  getRunInstrumentTrace,
  getRunInstruments,
  getRunManifest,
  getRunMetrics,
  getRunVerdict,
  getUpsets,
  listArtifacts,
  listRunNotes,
  listSuites,
  setRunFavorite,
  stopRun,
} from "@api/client";
import type { RunRow, UpsetSummary } from "@api/types";
import { formatTimestamp } from "../utils/format";
import RunPage from "./RunPage";
import { pending, spinners } from "../test/queries";

vi.mock("@api/client", () => ({
  abortRun: vi.fn(),
  addRunNote: vi.fn(),
  artifactUrl: (runId: string, path: string) => `/api/runs/${runId}/artifacts/${path}`,
  deleteRunNote: vi.fn(),
  getArtifactText: vi.fn(),
  getDaqRecording: vi.fn(),
  getDaqWindow: vi.fn(),
  getRun: vi.fn(),
  getRunInstrumentTrace: vi.fn(),
  getRunInstruments: vi.fn(),
  getRunManifest: vi.fn(),
  getRunMetrics: vi.fn(),
  getRunVerdict: vi.fn(),
  getUpsets: vi.fn(),
  listArtifacts: vi.fn(),
  listRunNotes: vi.fn(),
  listSuites: vi.fn(),
  runEventsUrl: (runId: string) => `/api/runs/${runId}/events`,
  runExportUrl: (runId: string) => `/api/runs/${runId}/export`,
  runReportUrl: (runId: string) => `/api/runs/${runId}/report`,
  setRunFavorite: vi.fn(),
  stopRun: vi.fn(),
}));

const NO_UPSETS: UpsetSummary = {
  events: [],
  instruments: [],
  stop_after: 0,
  stopped_run: false,
  thresholds: {},
};

beforeEach(() => {
  vi.mocked(getUpsets).mockResolvedValue(NO_UPSETS);
});

const FINISHED: RunRow = {
  duration_s: 12,
  ended_at: "2026-01-01T00:00:12Z",
  fail_reason: "rail voltage out of tolerance",
  profile: "mock.yaml",
  run_dir: "/runs/run-1",
  run_id: "run-1",
  started_at: "2026-01-01T00:00:00Z",
  status: "failed",
  suite: "thermal_cycle",
  target: "10.0.0.4",
  unit_serial: "SN-42",
  verdict: "FAIL",
};

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

describe("RunPage campaign", () => {
  beforeEach(() => {
    vi.mocked(listSuites).mockResolvedValue({ errors: [], suites: [] });
    vi.mocked(getRunMetrics).mockResolvedValue({ count: 0, records: [], run_id: "run-1" });
    vi.mocked(getRunVerdict).mockResolvedValue({ passed: true } as never);
    vi.mocked(getRunManifest).mockResolvedValue({} as never);
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [], run_dir: "", run_id: "run-1" });
    vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
  });

  it("adds the run to the favorites", async () => {
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    vi.mocked(setRunFavorite).mockResolvedValue({ favorite: true, run_id: "run-1" });
    renderPage();
    const star = await screen.findByRole("button", { name: "Add to favorites" });
    expect(star).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(star);
    expect(setRunFavorite).toHaveBeenCalledWith("run-1", true);
  });

  it("takes a favorite back out of the favorites", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, favorite: true });
    vi.mocked(setRunFavorite).mockResolvedValue({ favorite: false, run_id: "run-1" });
    renderPage();
    const star = await screen.findByRole("button", { name: "Favorite" });
    expect(star).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(star);
    expect(setRunFavorite).toHaveBeenCalledWith("run-1", false);
  });

  it("names the campaign that groups the run's suite", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...FINISHED,
      campaign: { key: "hardware", title: "Hardware Bench" },
    });
    renderPage();

    const link = await screen.findByRole("link", { name: "Hardware Bench" });
    expect(link).toHaveAttribute("href", "/tests?view=campaigns&campaign=hardware");
  });

  it("shows a dash when the run's suite is in no campaign", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, campaign: null });
    renderPage();

    await screen.findByRole("heading", { name: "thermal_cycle" });
    expect(screen.queryByRole("link", { name: /Bench/ })).not.toBeInTheDocument();
  });
});

describe("RunPage", () => {
  beforeEach(() => {
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    vi.mocked(listSuites).mockResolvedValue({ errors: [], suites: [] });
    vi.mocked(getRunMetrics).mockResolvedValue({
      count: RECORDS.length,
      records: RECORDS as never,
      run_id: "run-1",
    });
    vi.mocked(getRunVerdict).mockResolvedValue({
      passed: false,
      reason: "rail voltage out of tolerance",
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
    vi.mocked(getArtifactText).mockResolvedValue("boot ok\nERROR rail low");
    vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
  });

  it("shows the run's identity and timings", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "thermal_cycle" })).toBeInTheDocument();
    expect(screen.getByText("run-1")).toBeInTheDocument();
    expect(screen.getByText("mock.yaml")).toBeInTheDocument();
    expect(screen.getByText("SN-42")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.4")).toBeInTheDocument();
    expect(screen.getByText("12s")).toBeInTheDocument();
    expect(screen.getByText(formatTimestamp("2026-01-01T00:00:12Z"))).toBeInTheDocument();
  });

  it("hydrates the verdict of a finished run from the stored files", async () => {
    renderPage();
    expect(await screen.findByText("FAILED")).toBeInTheDocument();
    expect(screen.getByLabelText("Iterations")).toBeInTheDocument();
    expect(screen.getByText("bench-1")).toBeInTheDocument();
  });

  it("calls out the anomalies the run reported", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "1 anomaly" })).toBeInTheDocument();
    expect(screen.getByText("out_of_envelope")).toBeInTheDocument();
  });

  it("replays the stored metrics records as iterations", async () => {
    renderPage();
    await screen.findByText("FAILED");
    await userEvent.click(screen.getByRole("tab", { name: "iterations" }));
    expect(screen.getByText("rail low")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "rail.volts" })).toBeInTheDocument();
    expect(screen.getByText("2.9")).toBeInTheDocument();
  });

  it("discovers the metric series from the stored records", async () => {
    renderPage();
    await screen.findByText("FAILED");
    await userEvent.click(screen.getByRole("tab", { name: "metrics" }));
    expect(screen.getByRole("heading", { name: "rail.volts" })).toBeInTheDocument();
  });

  it("reads the log of a finished run out of test.log", async () => {
    renderPage();
    await screen.findByText("FAILED");
    await userEvent.click(screen.getByRole("tab", { name: "log" }));
    expect(await screen.findByText("boot ok")).toBeInTheDocument();
    expect(screen.getByText("ERROR rail low")).toBeInTheDocument();
  });

  it("hides the run controls once the run has finished", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "thermal_cycle" });
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Abort" })).not.toBeInTheDocument();
  });

  it("offers a finished run as an archive", async () => {
    renderPage();
    const link = await screen.findByRole("link", { name: "Export run" });
    expect(link).toHaveAttribute("href", "/api/runs/run-1/export");
    expect(link).toHaveAttribute("download");
  });

  it("offers a finished run as a report", async () => {
    renderPage();
    const link = await screen.findByRole("link", { name: "Download report" });
    expect(link).toHaveAttribute("href", "/api/runs/run-1/report");
    expect(link).toHaveAttribute("download");
  });

  it("opens a run still in flight on its log", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, status: "running", ended_at: null });
    renderPage();
    await screen.findByRole("button", { name: "Stop" });
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /log/ })).toHaveAttribute("aria-selected", "true")
    );
  });

  it("opens a finished run on its overview", async () => {
    renderPage();
    await screen.findByText("FAILED");
    expect(screen.getByRole("tab", { name: "overview" })).toHaveAttribute("aria-selected", "true");
  });

  it("does not offer to export a run that is still writing artifacts", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, status: "running", ended_at: null });
    renderPage();
    await screen.findByRole("button", { name: "Stop" });
    expect(screen.queryByRole("link", { name: "Export run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Download report" })).not.toBeInTheDocument();
  });

  it("stops a live run once the operator confirms", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, status: "running", ended_at: null });
    vi.mocked(stopRun).mockResolvedValue({ run_id: "run-1", status: "stopping" });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(stopRun).toHaveBeenCalledWith("run-1");
  });

  it("spins while the run row is being read", () => {
    vi.mocked(getRun).mockReturnValue(pending());
    renderPage();
    expect(spinners()).toHaveLength(1);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("reports a run id nothing answers to", async () => {
    vi.mocked(getRun).mockRejectedValue(new Error("unknown run 'run-1'"));
    renderPage();
    expect(await screen.findByText("Run not found")).toBeInTheDocument();
  });

  it("offers no snapshots tab for a run that recorded no images", async () => {
    renderPage();
    await screen.findByRole("tablist");
    expect(screen.queryByRole("tab", { name: /snapshots/ })).not.toBeInTheDocument();
  });

  it("offers no traces tab for a run that recorded no traces", async () => {
    renderPage();
    await screen.findByRole("tablist");
    expect(screen.queryByRole("tab", { name: /traces/ })).not.toBeInTheDocument();
  });
});

describe("RunPage traces", () => {
  beforeEach(() => {
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    vi.mocked(listSuites).mockResolvedValue({ errors: [], suites: [] });
    vi.mocked(getRunVerdict).mockResolvedValue({ passed: true } as never);
    vi.mocked(getRunManifest).mockResolvedValue({} as never);
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [], run_dir: "", run_id: "run-1" });
    vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
    vi.mocked(getRunMetrics).mockResolvedValue({
      count: 1,
      records: [
        {
          elapsed_run_s: 1,
          iteration: 1,
          kind: "iteration",
          metrics: {
            images: ["frames/snapshot_0001.png"],
            traces: ["traces/capture_0001.png"],
          },
          success: true,
          timestamp: 1767225600,
        },
      ] as never,
      run_id: "run-1",
    });
  });

  it("offers a traces tab of its own, counting only the traces", async () => {
    renderPage();
    expect(await screen.findByRole("tab", { name: /traces/ })).toHaveTextContent("1");
    expect(screen.getByRole("tab", { name: /snapshots/ })).toHaveTextContent("1");
  });

  it("draws the recorded traces rather than the snapshots", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: /traces/ }));

    const drawn = screen.getAllByRole("img");
    expect(drawn).toHaveLength(1);
    expect(drawn[0]).toHaveAttribute("src", "/api/runs/run-1/artifacts/traces/capture_0001.png");
  });
});

describe("RunPage snapshots", () => {
  beforeEach(() => {
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    vi.mocked(listSuites).mockResolvedValue({ errors: [], suites: [] });
    vi.mocked(getRunVerdict).mockResolvedValue({ passed: true } as never);
    vi.mocked(getRunManifest).mockResolvedValue({} as never);
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [], run_dir: "", run_id: "run-1" });
    vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
    vi.mocked(getRunMetrics).mockResolvedValue({
      count: 2,
      records: [
        {
          elapsed_run_s: 1,
          iteration: 1,
          kind: "iteration",
          metrics: { camera: { mean_luma: 120 }, images: ["frames/snapshot_0001.png"] },
          success: true,
          timestamp: 1767225600,
        },
        {
          elapsed_run_s: 2,
          iteration: 2,
          kind: "iteration",
          metrics: { camera: { mean_luma: 118 }, images: ["frames/snapshot_0002.png"] },
          success: true,
          timestamp: 1767225601,
        },
      ] as never,
      run_id: "run-1",
    });
  });

  it("offers a snapshots tab counting the images the run recorded", async () => {
    renderPage();
    const tab = await screen.findByRole("tab", { name: /snapshots/ });
    expect(tab).toHaveTextContent("2");
  });

  it("draws every recorded image once the tab is opened", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: /snapshots/ }));

    const images = screen.getAllByRole("img");
    expect(images).toHaveLength(2);
    expect(images[0]).toHaveAttribute("src", "/api/runs/run-1/artifacts/frames/snapshot_0001.png");
  });
});

describe("RunPage moves between its views", () => {
  beforeEach(() => {
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    vi.mocked(listSuites).mockResolvedValue({ errors: [], suites: [] });
    vi.mocked(getRunVerdict).mockResolvedValue({ passed: false } as never);
    vi.mocked(getRunManifest).mockResolvedValue({} as never);
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [], run_dir: "", run_id: "run-1" });
    vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
    vi.mocked(getRunMetrics).mockResolvedValue({
      count: RECORDS.length,
      records: RECORDS as never,
      run_id: "run-1",
    });
  });

  it("opens the iterations on the one picked from the overview's map", async () => {
    // jsdom does not scroll, and the table scrolls the picked row into view.
    Element.prototype.scrollIntoView = vi.fn();
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: /^#2 · / }));

    expect(screen.getByRole("tab", { name: /iterations/ })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    expect(screen.getByText("rail low")).toBeInTheDocument();
  });

  it("opens the traces tab from the timeline's row in the artifact list", async () => {
    vi.mocked(getRunMetrics).mockResolvedValue({
      count: 1,
      records: [
        {
          iteration: 1,
          kind: "iteration",
          metrics: { traces: ["traces/captures.jsonl"] },
          success: true,
          timestamp: 1767225600,
        },
      ] as never,
      run_id: "run-1",
    });
    vi.mocked(listArtifacts).mockResolvedValue({
      artifacts: [{ path: "traces/captures.jsonl", size: 80, text: true }],
      run_dir: "",
      run_id: "run-1",
    });
    vi.mocked(getArtifactText).mockResolvedValue(JSON.stringify({ channels: ["SCL"] }));
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: /artifacts/ }));

    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));

    expect(screen.getByRole("tab", { name: /traces/ })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("No traces")).toBeInTheDocument();
  });

  it("offers a captures tab for a run that captured waveforms", async () => {
    vi.mocked(listArtifacts).mockResolvedValue({
      artifacts: [
        { path: "captures/capture_0002.csv", size: 80, text: true },
        { path: "captures/capture_0001.csv", size: 80, text: true },
        { path: "captures/readme.txt", size: 8, text: true },
      ],
      run_dir: "",
      run_id: "run-1",
    });
    vi.mocked(getArtifactText).mockResolvedValue("t_s,ch0\n0,1\n0.001,2");
    renderPage();

    const tab = await screen.findByRole("tab", { name: /captures/ });
    expect(tab).toHaveTextContent("2");
    await userEvent.click(tab);

    expect(await screen.findByText(/2 samples/)).toBeInTheDocument();
    expect(getArtifactText).toHaveBeenCalledWith("run-1", "captures/capture_0001.csv");
  });

  it("offers an instruments tab for a run that recorded its instruments", async () => {
    vi.mocked(listArtifacts).mockResolvedValue({
      artifacts: [{ path: "instruments.json", size: 80, text: true }],
      run_dir: "",
      run_id: "run-1",
    });
    vi.mocked(getRunInstruments).mockResolvedValue({ instruments: [], interval_s: 2, ticks: 6 });
    renderPage();

    await userEvent.click(await screen.findByRole("tab", { name: /instruments/ }));

    expect(await screen.findByText(/Read every 2s, 6 times over the run/)).toBeInTheDocument();
  });

  it("charts the bench's recorded readings beside the suite's own series", async () => {
    vi.mocked(listArtifacts).mockResolvedValue({
      artifacts: [{ path: "instruments.jsonl", size: 80, text: true }],
      run_dir: "",
      run_id: "run-1",
    });
    vi.mocked(getRunInstrumentTrace).mockResolvedValue([
      { at: "2026-01-01T00:00:01.000Z", instrument: "psu", t: 1, values: { voltage: 5 } },
      { at: "2026-01-01T00:00:03.000Z", instrument: "psu", t: 3, values: { voltage: 5.1 } },
    ]);
    renderPage();
    await screen.findByText("FAILED");
    await waitFor(() => expect(getRunInstrumentTrace).toHaveBeenCalledWith("run-1"));

    await userEvent.click(screen.getByRole("tab", { name: "metrics" }));
    await userEvent.click(screen.getByRole("button", { name: /measurements/i }));

    expect(await screen.findByLabelText("psu.voltage")).toBeInTheDocument();
    expect(screen.getByLabelText("rail.volts")).toBeInTheDocument();
  });

  it("links the location and session the run was started from to the history", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...FINISHED,
      location: "Bench 3",
      operator: "Ada",
      session: "TID 7",
    });
    renderPage();

    expect(await screen.findByRole("link", { name: "Bench 3" })).toHaveAttribute(
      "href",
      "/history?location=Bench%203"
    );
    expect(screen.getByRole("link", { name: "TID 7" })).toHaveAttribute(
      "href",
      "/history?session=TID%207"
    );
    expect(screen.getByText("Ada")).toBeInTheDocument();
  });

  it("adds a note to the run, and deletes one once confirmed", async () => {
    vi.mocked(listRunNotes).mockResolvedValue({
      notes: [
        { author: "Ada", body: "rail sagged at 40C", created_at: "2026-01-01T00:00:00Z", id: 7 },
      ],
    });
    vi.mocked(addRunNote).mockResolvedValue({
      author: null,
      body: "retest tomorrow",
      created_at: "2026-01-01T00:01:00Z",
      id: 8,
    });
    vi.mocked(deleteRunNote).mockResolvedValue({ deleted: true, id: "7" });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: /notes/ }));

    await userEvent.type(screen.getByLabelText("Add a note"), "retest tomorrow");
    await userEvent.click(screen.getByRole("button", { name: "Add note" }));
    await waitFor(() =>
      expect(addRunNote).toHaveBeenCalledWith(
        "run-1",
        expect.objectContaining({ body: "retest tomorrow" })
      )
    );

    await userEvent.click(screen.getByRole("button", { name: "Delete note 7" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(deleteRunNote).toHaveBeenCalledWith("run-1", 7));
  });
});

describe("RunPage aborts a live run", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, status: "running", ended_at: null });
    vi.mocked(listSuites).mockResolvedValue({ errors: [], suites: [] });
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [], run_dir: "", run_id: "run-1" });
    vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
    vi.mocked(abortRun).mockResolvedValue({ run_id: "run-1", status: "aborting" });
  });

  it("aborts once the operator confirms", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Abort" }));
    expect(
      screen.getByText("Abort this run? It is terminated without a verdict.")
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(abortRun).toHaveBeenCalledWith("run-1");
  });

  it("does nothing when the operator dismisses the confirmation", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Abort" }));

    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByText(/Abort this run\?/)).not.toBeInTheDocument();
    expect(abortRun).not.toHaveBeenCalled();
  });
});

describe("RunPage run info", () => {
  beforeEach(() => {
    vi.mocked(listSuites).mockResolvedValue({ errors: [], suites: [] });
    vi.mocked(getRunMetrics).mockResolvedValue({ count: 0, records: [], run_id: "run-1" });
    vi.mocked(getRunVerdict).mockResolvedValue({ passed: true } as never);
    vi.mocked(getRunManifest).mockResolvedValue({} as never);
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [], run_dir: "", run_id: "run-1" });
    vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
  });

  it("folds the run's details away while it is in flight", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, ended_at: null, status: "running" });
    renderPage();
    const toggle = await screen.findByRole("button", { name: "Run info" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("campaign")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
  });

  it("opens them again when asked", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, ended_at: null, status: "running" });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Run info" }));
    expect(screen.getByRole("button", { name: "Run info" })).toHaveAttribute(
      "aria-expanded",
      "true"
    );
    expect(screen.getByText("campaign")).toBeInTheDocument();
  });

  it("always shows a finished run's details, with nothing to fold them", async () => {
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    renderPage();
    expect(await screen.findByText("campaign")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run info" })).not.toBeInTheDocument();
  });

  it("reads the run's files again when it ends, so what it wrote at the end is offered", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, ended_at: null, status: "running" });
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [], run_dir: "", run_id: "run-1" });
    renderPage();
    await screen.findByRole("button", { name: "Run info" });
    expect(screen.queryByRole("tab", { name: /instruments/i })).not.toBeInTheDocument();
    vi.mocked(listArtifacts).mockResolvedValue({
      artifacts: [{ path: "instruments.json", size: 10, text: true }],
      run_dir: "",
      run_id: "run-1",
    });
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    expect(
      await screen.findByRole("tab", { name: /instruments/i }, { timeout: 4000 })
    ).toBeInTheDocument();
  });

  it("opens the details of a run that was folded when it ends", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...FINISHED, ended_at: null, status: "running" });
    renderPage();
    await screen.findByRole("button", { name: "Run info" });
    expect(screen.queryByText("campaign")).not.toBeInTheDocument();
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    await waitFor(() => expect(screen.getByText("campaign")).toBeInTheDocument(), {
      timeout: 4000,
    });
    expect(screen.queryByRole("button", { name: "Run info" })).not.toBeInTheDocument();
  });
});

describe("RunPage upsets", () => {
  beforeEach(() => {
    vi.mocked(getRun).mockResolvedValue(FINISHED);
    vi.mocked(listSuites).mockResolvedValue({ errors: [], suites: [] });
    vi.mocked(getRunMetrics).mockResolvedValue({ count: 0, records: [], run_id: "run-1" });
    vi.mocked(getRunVerdict).mockResolvedValue({ passed: true } as never);
    vi.mocked(getRunManifest).mockResolvedValue({} as never);
    vi.mocked(listArtifacts).mockResolvedValue({ artifacts: [], run_dir: "", run_id: "run-1" });
    vi.mocked(listRunNotes).mockResolvedValue({ notes: [] });
  });

  it("offers no DAQ tab to a run that watched nothing and recorded nothing", async () => {
    renderPage();
    await screen.findByRole("tab", { name: /overview/i });
    expect(screen.queryByRole("tab", { name: /daq/i })).not.toBeInTheDocument();
  });

  it("offers the tab, with a count, to a run that recorded an upset", async () => {
    vi.mocked(getUpsets).mockResolvedValue({
      ...NO_UPSETS,
      events: [
        {
          at: "2026-01-01T00:00:05Z",
          channel: "1",
          direction: "high",
          elapsed_s: 5,
          file: "upsets/upset_0001.csv",
          index: 1,
          instance_id: "daq0",
          instrument: "daq.0",
          label: "Rail",
          limit: 2,
          post_s: 2,
          pre_s: 2,
          truncated: false,
          unit: "V",
          value: 3.3,
        },
      ],
    });
    renderPage();
    const tab = await screen.findByRole("tab", { name: /daq/i });
    expect(tab).toHaveTextContent("1");
    await userEvent.click(tab);
    expect(await screen.findByText("Rail")).toBeInTheDocument();
  });

  it("offers the tab to a run that recorded a DAQ even though no limit was crossed", async () => {
    vi.mocked(listArtifacts).mockResolvedValue({
      artifacts: [{ path: "daq/daq.0.001.json", size: 90, text: true }],
      run_dir: "",
      run_id: "run-1",
    });
    renderPage();
    expect(await screen.findByRole("tab", { name: /daq/i })).toBeInTheDocument();
  });

  it("opens the DAQ view in a window of its own", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    vi.mocked(getUpsets).mockResolvedValue({ ...NO_UPSETS, instruments: ["daq.0"] });
    vi.mocked(getDaqRecording).mockResolvedValue({
      instruments: [
        {
          channels: [{ key: "1", label: "CH 1", unit: "V" }],
          end_s: 5,
          instrument: "daq.0",
          rate_hz: 25,
          rows: 125,
          start_s: 0,
        },
      ],
      origin: 1,
    });
    vi.mocked(getDaqWindow).mockResolvedValue({ instrument: "daq.0", origin: 1, segments: [] });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: /daq/i }));
    await userEvent.click(await screen.findByRole("button", { name: "Open in a window" }));
    expect(open).toHaveBeenCalledWith(
      expect.stringMatching(/#\/runs\/run-1\/daq$/),
      "gauntlet-daq-run-1",
      expect.stringContaining("popup")
    );
    open.mockRestore();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getArtifactText,
  getRun,
  getRunManifest,
  getRunMetrics,
  getRunVerdict,
  listArtifacts,
  listRunNotes,
  stopRun,
} from "@api/client";
import type { RunRow } from "@api/types";
import RunPage from "./RunPage";
import { pending, spinners } from "../test/queries";

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

describe("RunPage", () => {
  beforeEach(() => {
    vi.mocked(getRun).mockResolvedValue(FINISHED);
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

  it("shows the run's identity, status and timings", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "thermal_cycle" })).toBeInTheDocument();
    expect(screen.getByText("run-1")).toBeInTheDocument();
    expect(screen.getByText("mock.yaml")).toBeInTheDocument();
    expect(screen.getByText("SN-42")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.4")).toBeInTheDocument();
    expect(screen.getByText("12s")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("FAIL");
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
    expect(screen.getByLabelText("rail.volts")).toBeInTheDocument();
  });

  it("reads the log of a finished run out of test.log", async () => {
    renderPage();
    await screen.findByText("FAILED");
    await userEvent.click(screen.getByRole("tab", { name: "log" }));
    expect(await screen.findByText("boot ok")).toBeInTheDocument();
    expect(screen.getByText("ERROR rail low")).toBeInTheDocument();
  });

  it("disables the run controls once the run has finished", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "thermal_cycle" });
    expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Abort" })).toBeDisabled();
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
});

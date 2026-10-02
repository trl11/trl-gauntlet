import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getDaqRecording, getRun, getUpsets } from "@api/client";
import type { RunRow, UpsetSummary } from "@api/types";
import { runUpsets } from "../test/fixtures";
import DaqWindowPage from "./DaqWindowPage";

vi.mock("@api/client", () => ({
  artifactUrl: (runId: string, path: string) => `/api/runs/${runId}/artifacts/${path}`,
  getArtifactText: vi.fn().mockResolvedValue(""),
  getDaqRecording: vi.fn(),
  getDaqWindow: vi.fn().mockResolvedValue({ instrument: "daq.0", origin: 1, segments: [] }),
  getRun: vi.fn(),
  getUpsets: vi.fn(),
  getUpsetTrace: vi.fn(),
  runEventsUrl: (runId: string) => `/api/runs/${runId}/events`,
}));

const FINISHED = {
  run_id: "run-1",
  started_at: "2026-01-01T00:00:00Z",
  status: "passed",
  suite: "thermal_cycle",
} as RunRow;

function renderWindow() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/runs/run-1/daq"]}>
        <Routes>
          <Route path="/runs/:runId/daq" element={<DaqWindowPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.mocked(getRun).mockResolvedValue(FINISHED);
  vi.mocked(getUpsets).mockResolvedValue(runUpsets);
  vi.mocked(getDaqRecording).mockResolvedValue({ instruments: [], origin: null });
});

describe("DaqWindowPage", () => {
  it("shows the DAQ view and the run's name, and nothing of the run's details", async () => {
    renderWindow();
    expect(await screen.findByText("thermal_cycle")).toBeInTheDocument();
    expect(await screen.findByText(/1 of 1 events/)).toBeInTheDocument();
    expect(screen.queryByText("campaign")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("shows the recording of a run that crossed no limit", async () => {
    vi.mocked(getUpsets).mockResolvedValue({ ...runUpsets, events: [], instruments: [] });
    vi.mocked(getDaqRecording).mockResolvedValue({
      instruments: [
        { channels: [], end_s: 1, instrument: "daq.0", rate_hz: 25, rows: 25, start_s: 0 },
      ],
      origin: 1,
    });
    renderWindow();
    expect(await screen.findByText("thermal_cycle")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("No DAQ")).not.toBeInTheDocument());
  });

  it("says so when the run has no DAQ", async () => {
    const none: UpsetSummary = { ...runUpsets, events: [], instruments: [] };
    vi.mocked(getUpsets).mockResolvedValue(none);
    renderWindow();
    expect(await screen.findByText("No DAQ")).toBeInTheDocument();
  });

  it("says so when there is no such run", async () => {
    vi.mocked(getRun).mockRejectedValue(new Error("unknown run"));
    renderWindow();
    expect(await screen.findByText("Run not found")).toBeInTheDocument();
  });
});

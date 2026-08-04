import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Instrument, RunRow, Suite, SystemData, Unit } from "@api/types";

import DashboardPage from "./DashboardPage";
import { pending, spinners } from "../test/queries";

const abortRun = vi.fn();
const getSystemData = vi.fn();
const listCapabilities = vi.fn();
const listInstruments = vi.fn();
const listRuns = vi.fn();
const listSuites = vi.fn();
const listUnits = vi.fn();
const stopRun = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    abortRun: (...args: unknown[]) => abortRun(...args),
    getSystemData: () => getSystemData(),
    listCapabilities: () => listCapabilities(),
    listInstruments: () => listInstruments(),
    listRuns: (...args: unknown[]) => listRuns(...args),
    listSuites: () => listSuites(),
    listUnits: () => listUnits(),
    stopRun: (...args: unknown[]) => stopRun(...args),
  };
});

function run(overrides: Partial<RunRow> = {}): RunRow {
  return {
    duration_s: 12,
    ended_at: new Date().toISOString(),
    fail_reason: null,
    profile: "mock.yaml",
    run_dir: "/runs/thermal_cycle/r1",
    run_id: "r1",
    started_at: new Date().toISOString(),
    status: "passed",
    suite: "thermal_cycle",
    target: null,
    unit_serial: "HC-001",
    verdict: "PASS",
    ...overrides,
  };
}

function systemData(): SystemData {
  return {
    cpu_percent: 42.5,
    cpu_per_core: [40, 45],
    disks: [{ free: 100, mount: "/", percent: 91, total: 1000, used: 900 }],
    load_avg: [0.5, 0.4, 0.3],
    memory: { available: 4, percent: 55, total: 16, used: 12 },
    process_count: 120,
    swap: { percent: 0, total: 0, used: 0 },
    temperatures: [
      { celsius: 41.2, label: "cpu" },
      { celsius: 72.5, label: "gpu" },
    ],
    uptime_s: 3600,
  };
}

function instrument(): Instrument {
  return {
    available: true,
    commands: [],
    description: "Bench supply",
    instance_id: "psu-1",
    kind: "psu",
    name: "psu",
    unavailable_reason: "",
    state: { output_enabled: false, voltage_v: 12 },
  };
}

function unit(overrides: Partial<Unit> = {}): Unit {
  return {
    failed: 2,
    first_seen: "2026-01-01T00:00:00Z",
    last_run: { ended_at: "2026-01-08T09:00:00Z", run_id: "r1", status: "passed", suite: "s" },
    last_seen: "2026-01-08T09:00:00Z",
    note_count: 0,
    passed: 5,
    run_count: 7,
    serial: "HC-001",
    ...overrides,
  };
}

function suite(): Suite {
  return {
    apiVersion: 1,
    category: "thermal",
    conformance_profile: "standard",
    description: "Cycle the chamber.",
    directory: "/suites/thermal_cycle",
    exec: {
      args: {},
      command: ["python"],
      env: {},
      graceful_stop_signal: "SIGINT",
      profile_schema_command: [],
      workdir: ".",
    },
    key: "thermal_cycle",
    overrides: [],
    produces: ["verdict"],
    profiles: "profiles",
    requires: ["chamber"],
    supports: { target: true, unit_serial: true },
    title: "Thermal cycle",
  };
}

/** The units table, which the page renders alongside the recent runs table. */
async function unitsTable(): Promise<HTMLElement> {
  const header = await screen.findByRole("columnheader", { name: "Last tested" });
  return header.closest("table") as HTMLElement;
}

function renderDashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  abortRun.mockResolvedValue({ run_id: "r2", status: "aborting" });
  getSystemData.mockResolvedValue(systemData());
  listCapabilities.mockResolvedValue({
    capabilities: [{ available: "true", instance_id: "c1", name: "chamber" }],
  });
  listInstruments.mockResolvedValue({ instruments: [instrument()] });
  listRuns.mockResolvedValue({ runs: [run()] });
  listSuites.mockResolvedValue({ errors: [], suites: [suite()] });
  listUnits.mockResolvedValue({ units: [unit()] });
  stopRun.mockResolvedValue({ run_id: "r2", status: "stopping" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("DashboardPage", () => {
  it("says so when nothing is running and nothing has been on the bench", async () => {
    listRuns.mockResolvedValue({ runs: [] });
    listUnits.mockResolvedValue({ units: [] });
    renderDashboard();
    expect(await screen.findByText("Nothing running")).toBeInTheDocument();
    expect(screen.getByText("No unit has been on the bench yet.")).toBeInTheDocument();
  });

  it("heads each column of the units table", async () => {
    renderDashboard();
    const table = await unitsTable();
    for (const header of ["Serial", "Last tested", "Runs", "Passed", "Failed", "Last run"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
  });

  it("counts every run of a unit, not only the ones the dashboard lists", async () => {
    renderDashboard();
    const rows = within(await unitsTable()).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("7");
    expect(rows[1]).toHaveTextContent("5");
    expect(rows[1]).toHaveTextContent("2");
  });

  it("orders the units by when each was last tested", async () => {
    listUnits.mockResolvedValue({
      units: [
        unit({ last_seen: "2026-01-02T00:00:00Z", serial: "OLDER" }),
        unit({ last_seen: "2026-01-09T00:00:00Z", serial: "NEWER" }),
      ],
    });
    renderDashboard();
    const serials = within(await unitsTable())
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(serials).toEqual(["NEWER", "OLDER"]);
  });

  it("shows the last unit under test when no run is in flight", async () => {
    renderDashboard();
    expect(await screen.findByText("serial")).toBeInTheDocument();
    expect(screen.getAllByText("HC-001").length).toBeGreaterThan(0);
  });

  it("lists the host health tiles", async () => {
    renderDashboard();
    expect(await screen.findByText("42.5%")).toBeInTheDocument();
    expect(screen.getByText("2 cores")).toBeInTheDocument();
    expect(screen.getByText("72.5 °C")).toBeInTheDocument();
    expect(screen.getByText("gpu")).toBeInTheDocument();
  });

  it("links each instrument to the instruments page", async () => {
    renderDashboard();
    const link = await screen.findByRole("link", { name: "psu, available" });
    expect(link).toHaveAttribute("href", "/instruments");
  });

  it("lists the recent runs", async () => {
    renderDashboard();
    expect(await screen.findByText("Recent runs")).toBeInTheDocument();
    expect((await screen.findAllByText("thermal_cycle")).length).toBeGreaterThan(0);
  });

  it("links every mention of a unit to its own page", async () => {
    renderDashboard();
    const links = await screen.findAllByRole("link", { name: "HC-001" });
    for (const link of links) expect(link).toHaveAttribute("href", "/units/HC-001");
  });

  it("reports discovery errors", async () => {
    listSuites.mockResolvedValue({ errors: ["suites/broken: missing key"], suites: [] });
    renderDashboard();
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Suite discovery reported 1 problem(s)");
    expect(banner).toHaveTextContent("suites/broken: missing key");
  });

  it("shows a live run with its elapsed time and controls", async () => {
    listRuns.mockResolvedValue({
      runs: [run({ ended_at: null, run_id: "r2", status: "running" })],
    });
    renderDashboard();
    expect(await screen.findByRole("link", { name: "thermal_cycle" })).toHaveAttribute(
      "href",
      "/runs/r2"
    );
    expect(screen.getByText("Elapsed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
  });

  it("stops a run only once the operator confirms", async () => {
    listRuns.mockResolvedValue({
      runs: [run({ ended_at: null, run_id: "r2", status: "running" })],
    });
    renderDashboard();
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(stopRun).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(stopRun).toHaveBeenCalledWith("r2"));
  });

  it("spins every section while its query is in flight", () => {
    getSystemData.mockReturnValue(pending());
    listInstruments.mockReturnValue(pending());
    listRuns.mockReturnValue(pending());
    listSuites.mockReturnValue(pending());
    listUnits.mockReturnValue(pending());
    renderDashboard();
    // Host health, the active-run card, the instrument list and the units table.
    expect(spinners()).toHaveLength(4);
    expect(screen.queryByText("Nothing running")).not.toBeInTheDocument();
  });

  it("says so when host telemetry cannot be read", async () => {
    getSystemData.mockRejectedValue(new Error("no /proc"));
    renderDashboard();
    expect(await screen.findByText("Host telemetry is unavailable.")).toBeInTheDocument();
  });

  it("says so when no instrument is registered", async () => {
    listInstruments.mockResolvedValue({ instruments: [] });
    renderDashboard();
    expect(await screen.findByText("No instruments")).toBeInTheDocument();
  });

  it("leaves the run alone when the operator dismisses the abort", async () => {
    listRuns.mockResolvedValue({
      runs: [run({ ended_at: null, run_id: "r2", status: "running" })],
    });
    renderDashboard();
    await userEvent.click(await screen.findByRole("button", { name: "Abort" }));
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(abortRun).not.toHaveBeenCalled();
  });
});

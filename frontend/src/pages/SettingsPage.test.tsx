import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SystemData } from "@api/types";

import SettingsPage from "./SettingsPage";
import { pending, spinners } from "../test/queries";

const getHealth = vi.fn();
const getSettings = vi.fn();
const getSystemInfo = vi.fn();
const getSystemData = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getHealth: () => getHealth(),
    getSettings: () => getSettings(),
    getSystemInfo: () => getSystemInfo(),
    getSystemData: () => getSystemData(),
  };
});

function systemData(): SystemData {
  return {
    cpu_percent: 42.5,
    cpu_per_core: [40, 45],
    disk: { free: 500, mount: "/workspaces/gauntlet", percent: 40, total: 1000, used: 400 },
    disks: [
      { free: 100, mount: "/", percent: 91, total: 1000, used: 900 },
      { free: 500, mount: "/workspaces/gauntlet", percent: 40, total: 1000, used: 400 },
    ],
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

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SettingsPage />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getHealth.mockResolvedValue({ status: "ok" });
  getSystemData.mockResolvedValue(systemData());
  getSettings.mockResolvedValue({
    data_dir: "/home/dev/.local/share/gauntlet",
    default_target: "",
    host: "127.0.0.1",
    log_level: "info",
    open_browser: false,
    port: 7100,
    profiles_dir: "/home/dev/.local/share/gauntlet/profiles",
    profiles_dir_override: null,
    runs_dir: "/home/dev/.local/share/gauntlet/runs",
    runs_dir_override: null,
    suite_roots: ["/workspaces/gauntlet/suites"],
  });
  getSystemInfo.mockResolvedValue({
    arch: "x86_64",
    boot_time: "2026-01-01T00:00:00Z",
    cpu_count: 8,
    cpu_model: "AMD Ryzen 7",
    gauntlet: "0.4.1",
    hostname: "bench-01",
    kernel: "6.8.0",
    memory_total_bytes: 34359738368,
    os: "Linux",
    python: "3.12.3",
    gauntlet_sdk: "0.4.1",
    contract_version: 1,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SettingsPage", () => {
  it("reports the health of the API", async () => {
    renderSettings();
    expect(await screen.findByText("healthy")).toBeInTheDocument();
    expect(screen.getByText("api server")).toBeInTheDocument();
  });

  it("says so when the API does not answer", async () => {
    getHealth.mockRejectedValue(new Error("cannot reach the Gauntlet API"));
    renderSettings();
    expect(await screen.findByText("unreachable")).toBeInTheDocument();
  });

  // The health probe answers on its own, so its row survives the rest failing.
  it("still reports the health of the API when the settings cannot be read", async () => {
    getSettings.mockRejectedValue(new Error("config.yaml is unreadable"));
    renderSettings();
    expect(await screen.findByText("healthy")).toBeInTheDocument();
    expect(screen.queryByText("port")).not.toBeInTheDocument();
  });

  it("shows the info section, with the host it runs on and the versions it runs", async () => {
    renderSettings();
    expect(await screen.findByText("bench-01")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Info" })).toBeInTheDocument();
    expect(screen.getAllByText("0.4.1")).toHaveLength(2);
  });

  it("shows the runtime section, with the settings the service answers on", async () => {
    renderSettings();
    expect(await screen.findByText("7100")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Runtime" })).toBeInTheDocument();
    expect(screen.getByText("info")).toBeInTheDocument();
  });

  it("shows the documentation section, linking to the API documentation", () => {
    renderSettings();
    expect(screen.getByRole("heading", { name: "Documentation" })).toBeInTheDocument();
    expect(screen.getByText("api documentation")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "swagger" })).toHaveAttribute("href", "/docs");
  });

  it("spins each section whose query is in flight", () => {
    getSettings.mockReturnValue(pending());
    getSystemInfo.mockReturnValue(pending());
    getSystemData.mockReturnValue(pending());
    renderSettings();
    // Host telemetry, the info block and the settings behind the runtime block.
    expect(spinners()).toHaveLength(3);
  });

  it("reports settings that could not be read", async () => {
    getSettings.mockRejectedValue(new Error("config.yaml is unreadable"));
    renderSettings();
    expect(await screen.findByText("config.yaml is unreadable")).toBeInTheDocument();
  });

  it("reports host facts that could not be read", async () => {
    getSystemInfo.mockRejectedValue(new Error("no /proc here"));
    renderSettings();
    expect(await screen.findByText("no /proc here")).toBeInTheDocument();
  });
});

describe("SettingsPage host telemetry", () => {
  it("lists the host figures as label and value", async () => {
    renderSettings();

    expect(await screen.findByText("Host stats")).toBeInTheDocument();
    // The heading is drawn before the query settles, so the first figure is
    // what says the readings arrived.
    expect(await screen.findByText("42.5%")).toBeInTheDocument();
    // Testing Library collapses the spacing the row is written with.
    expect(screen.getByText(/0\.50\s+0\.40\s+0\.30/)).toBeInTheDocument();
    expect(screen.getByText("72.5 °C · gpu")).toBeInTheDocument();
    expect(screen.getByText("55.0% · 12 B of 16 B")).toBeInTheDocument();
  });

  it("names the disk the runs are written to, not the fullest one", async () => {
    renderSettings();

    expect(
      await screen.findByText("40.0% · 500 B free on /workspaces/gauntlet")
    ).toBeInTheDocument();
    // The root filesystem is fuller and is not what Gauntlet writes to.
    expect(screen.queryByText(/91\.0%/)).not.toBeInTheDocument();
  });

  it("names only the hottest sensor", async () => {
    renderSettings();

    expect(await screen.findByText("72.5 °C · gpu")).toBeInTheDocument();
    expect(screen.queryByText(/41\.2/)).not.toBeInTheDocument();
  });

  it("shows a dash when the server could not read that disk", async () => {
    getSystemData.mockResolvedValue({ ...systemData(), disk: null });
    renderSettings();

    await screen.findByText("42.5%");
    expect(screen.getByText("cpu").closest("dl")).toHaveTextContent("disk-");
  });

  it("says the cpu is sampling until a second reading has been taken", async () => {
    getSystemData.mockResolvedValue({ ...systemData(), cpu_percent: null });
    renderSettings();

    expect(await screen.findByText("sampling")).toBeInTheDocument();
  });

  it("says so when host telemetry cannot be read", async () => {
    getSystemData.mockRejectedValue(new Error("no /proc"));
    renderSettings();

    expect(await screen.findByText("Host telemetry is unavailable.")).toBeInTheDocument();
  });

  it("keeps the rest of the page when telemetry fails", async () => {
    getSystemData.mockRejectedValue(new Error("no /proc"));
    renderSettings();

    await screen.findByText("Host telemetry is unavailable.");
    expect(screen.getByText("bench-01")).toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "./SettingsPage";
import { pending, spinners } from "../test/queries";

const getHealth = vi.fn();
const getSettings = vi.fn();
const getSystemInfo = vi.fn();
const getVersion = vi.fn();
const listSuites = vi.fn();
const rescanSuites = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getHealth: () => getHealth(),
    getSettings: () => getSettings(),
    getSystemInfo: () => getSystemInfo(),
    getVersion: () => getVersion(),
    listSuites: () => listSuites(),
    rescanSuites: () => rescanSuites(),
  };
});

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
  getSettings.mockResolvedValue({
    data_dir: "/home/dev/.local/share/gauntlet",
    default_target: "",
    host: "127.0.0.1",
    log_level: "info",
    open_browser: false,
    port: 7100,
    profiles_dir: "/home/dev/.local/share/gauntlet/profiles",
    profiles_user_dir: null,
    reports_base: null,
    runs_dir: "/home/dev/.local/share/gauntlet/runs",
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
  });
  getVersion.mockResolvedValue({
    contract_version: 1,
    gauntlet: "0.4.1",
    gauntlet_sdk: "0.4.1",
    platform: "Linux",
    python: "3.12.3",
  });
  listSuites.mockResolvedValue({ errors: [], suites: [{ key: "a" }, { key: "b" }] });
  rescanSuites.mockResolvedValue({ count: 2, errors: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SettingsPage", () => {
  it("reports the health of the API", async () => {
    renderSettings();
    expect(await screen.findByText("API HEALTHY")).toBeInTheDocument();
  });

  it("says so when the API does not answer", async () => {
    getHealth.mockRejectedValue(new Error("cannot reach the Gauntlet API"));
    renderSettings();
    expect(await screen.findByText("API UNREACHABLE")).toBeInTheDocument();
  });

  it("shows the service settings and the paths", async () => {
    renderSettings();
    expect(await screen.findByText("127.0.0.1")).toBeInTheDocument();
    expect(screen.getByText("7100")).toBeInTheDocument();
    expect(screen.getByText("/home/dev/.local/share/gauntlet/runs")).toBeInTheDocument();
    expect(screen.getByText("/workspaces/gauntlet/suites")).toBeInTheDocument();
  });

  it("links to the API documentation and the schemas", () => {
    renderSettings();
    expect(screen.getByRole("link", { name: "API documentation" })).toHaveAttribute(
      "href",
      "/docs"
    );
    expect(screen.getByRole("link", { name: "Contract schemas" })).toHaveAttribute(
      "href",
      "/api/schemas"
    );
  });

  it("shows every version the API reports", async () => {
    renderSettings();
    expect(await screen.findAllByText("0.4.1")).not.toHaveLength(0);
    expect(screen.getByText("contract")).toBeInTheDocument();
  });

  it("shows the host facts", async () => {
    renderSettings();
    expect(await screen.findByText("bench-01")).toBeInTheDocument();
    expect(screen.getByText("AMD Ryzen 7")).toBeInTheDocument();
    expect(screen.getByText("32.0 GB")).toBeInTheDocument();
  });

  it("counts discovered suites and lists manifest errors", async () => {
    listSuites.mockResolvedValue({ errors: ["suites/bad: missing key"], suites: [] });
    renderSettings();
    expect(await screen.findByText("0 suites, 1 manifest errors")).toBeInTheDocument();
    expect(screen.getByText("suites/bad: missing key")).toBeInTheDocument();
  });

  it("spins each section while its query is in flight", () => {
    getSettings.mockReturnValue(pending());
    getSystemInfo.mockReturnValue(pending());
    renderSettings();
    expect(spinners()).toHaveLength(2);
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

  it("rescans on demand", async () => {
    renderSettings();
    await screen.findByText("2 suites, 0 manifest errors");
    await userEvent.click(screen.getByRole("button", { name: /rescan/i }));
    expect(rescanSuites).toHaveBeenCalled();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "./SettingsPage";
import { pending, spinners } from "../test/queries";

const getHealth = vi.fn();
const getSettings = vi.fn();
const getSystemInfo = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getHealth: () => getHealth(),
    getSettings: () => getSettings(),
    getSystemInfo: () => getSystemInfo(),
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
});

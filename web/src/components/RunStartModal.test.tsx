import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@api/client";
import type { Suite } from "@api/types";

import RunStartModal from "./RunStartModal";

const startRun = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return { ...actual, startRun: (...args: unknown[]) => startRun(...args) };
});

function suite(partial: Partial<Suite> = {}): Suite {
  return {
    apiVersion: 1,
    category: "hardware",
    conformance_profile: "mock.yaml",
    description: "Chamber profile with per-segment pass/fail.",
    directory: "/suites/thermal_cycle",
    exec: {
      args: {},
      command: ["python", "-m", "thermal_cycle.cli"],
      env: {},
      graceful_stop_signal: "SIGUSR1",
      profile_schema_command: [],
      workdir: ".",
    },
    key: "thermal_cycle",
    overrides: [
      {
        choices: [],
        default: 60,
        flag: "--duration-s",
        help: "",
        label: "Duration",
        maximum: 600,
        minimum: 1,
        name: "duration_s",
        type: "number",
        unit: "s",
      },
      {
        choices: [],
        default: 3,
        flag: "--cycles",
        help: "",
        label: "Cycles",
        maximum: null,
        minimum: null,
        name: "cycles",
        type: "integer",
        unit: "",
      },
      {
        choices: [],
        default: false,
        flag: "--stop-on-failure",
        help: "",
        label: "Stop on failure",
        maximum: null,
        minimum: null,
        name: "stop_on_failure",
        type: "boolean",
        unit: "",
      },
    ],
    produces: ["metrics", "verdict"],
    profiles: "./profiles",
    profiles_available: [
      { description: "", name: "mock.yaml", path: "/p/mock.yaml", user_authored: false },
      { description: "", name: "long.yaml", path: "/p/long.yaml", user_authored: true },
    ],
    requires: [],
    supports: { target: true, unit_serial: false },
    title: "Thermal Cycle",
    ...partial,
  };
}

function renderModal(value = suite(), initialProfile: string | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tests"]}>
        <Routes>
          <Route
            path="/tests"
            element={
              <RunStartModal suite={value} initialProfile={initialProfile} onClose={vi.fn()} />
            }
          />
          <Route path="/runs/:runId" element={<p>run page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  startRun.mockResolvedValue({ run_id: "20260101T000000Z-0001" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("RunStartModal", () => {
  it("preselects the profile the operator picked in the catalog", () => {
    renderModal(suite(), "long.yaml");
    expect(screen.getByLabelText("Profile")).toHaveValue("long.yaml");
  });

  it("shows only the inputs the manifest says the suite supports", () => {
    renderModal();
    expect(screen.getByLabelText("Target")).toBeInTheDocument();
    expect(screen.queryByLabelText("Unit serial")).not.toBeInTheDocument();
  });

  it("shows the unit serial input when the manifest declares it", () => {
    renderModal(suite({ supports: { target: false, unit_serial: true } }));
    expect(screen.getByLabelText("Unit serial")).toBeInTheDocument();
    expect(screen.queryByLabelText("Target")).not.toBeInTheDocument();
  });

  it("summarises the argv the choices produce", async () => {
    const user = userEvent.setup();
    renderModal();
    await user.clear(screen.getByLabelText("Duration"));
    await user.type(screen.getByLabelText("Duration"), "90");
    expect(screen.getByText("--duration-s 90 --cycles 3")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Stop on failure"));
    expect(screen.getByText("--duration-s 90 --cycles 3 --stop-on-failure")).toBeInTheDocument();
  });

  it("blocks the run while an override is invalid", async () => {
    const user = userEvent.setup();
    renderModal();
    await user.type(screen.getByLabelText("Cycles"), ".5");
    expect(screen.getByRole("button", { name: "Start run" })).toBeDisabled();
    expect(startRun).not.toHaveBeenCalled();
  });

  it("posts the declared overrides and navigates to the run", async () => {
    const user = userEvent.setup();
    renderModal();
    await user.type(screen.getByLabelText("Target"), "192.168.55.1");
    await user.click(screen.getByRole("button", { name: "Start run" }));
    expect(startRun).toHaveBeenCalledWith({
      overrides: { cycles: 3, duration_s: 60, stop_on_failure: false },
      profile: "mock.yaml",
      suite: "thermal_cycle",
      target: "192.168.55.1",
      unit_serial: null,
    });
    expect(await screen.findByText("run page")).toBeInTheDocument();
  });

  it("shows the server's detail when the run is rejected", async () => {
    const user = userEvent.setup();
    startRun.mockRejectedValue(
      new ApiError(422, "capability 'chamber' is unavailable", "/api/runs")
    );
    renderModal();
    await user.click(screen.getByRole("button", { name: "Start run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "capability 'chamber' is unavailable"
    );
  });
});

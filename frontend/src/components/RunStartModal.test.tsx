import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@api/client";
import type { Suite, Unit } from "@api/types";

import RunStartModal from "./RunStartModal";

const getProfile = vi.fn();
const listUnits = vi.fn();
const startRun = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getProfile: (...args: unknown[]) => getProfile(...args),
    listUnits: (...args: unknown[]) => listUnits(...args),
    startRun: (...args: unknown[]) => startRun(...args),
  };
});

/** A unit as `GET /api/units` reports it, with only the fields the modal reads. */
function unit(serial: string, lastSeen: string | null): Unit {
  return {
    failed: 0,
    first_seen: lastSeen,
    last_run: null,
    last_seen: lastSeen,
    note_count: 0,
    passed: 0,
    run_count: 0,
    serial,
  };
}

function suite(partial: Partial<Suite> = {}): Suite {
  return {
    apiVersion: 1,
    category: "hardware",
    conformance_profile: "mock.yaml",
    default_metrics: [],
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
      {
        description: "",
        label: "Mock",
        name: "mock.yaml",
        path: "/p/mock.yaml",
        user_authored: false,
      },
      {
        description: "",
        label: "Long",
        name: "long.yaml",
        path: "/p/long.yaml",
        user_authored: true,
      },
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
  getProfile.mockResolvedValue({
    body: "cycles: 12\nduration_s: 300\nstop_on_failure: true\n",
    name: "mock.yaml",
    path: "/p/mock.yaml",
  });
  listUnits.mockResolvedValue({
    units: [unit("HC-001", "2026-01-01T00:00:00Z"), unit("HC-009", "2026-06-01T00:00:00Z")],
  });
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

  it("gathers the profile, target and serial under one common section", async () => {
    renderModal(suite({ supports: { target: true, unit_serial: true } }));
    const common = await screen.findByRole("region", { name: "Common settings" });
    expect(within(common).getByLabelText("Profile")).toBeInTheDocument();
    expect(within(common).getByLabelText("Target")).toBeInTheDocument();
    expect(within(common).getByLabelText("Unit serial")).toBeInTheDocument();
    expect(within(common).queryByLabelText("Duration (s)")).not.toBeInTheDocument();
  });

  it("offers the units already tested, most recently seen first", async () => {
    const { container } = renderModal(suite({ supports: { target: false, unit_serial: true } }));
    await waitFor(() => expect(container.querySelectorAll("datalist option")).toHaveLength(2));
    const serial = screen.getByLabelText("Unit serial");
    const options = container.querySelector(`datalist#${CSS.escape(serial.getAttribute("list")!)}`);
    expect([...(options?.querySelectorAll("option") ?? [])].map((o) => o.value)).toEqual([
      "HC-009",
      "HC-001",
    ]);
  });

  it("still accepts a serial no unit has yet", async () => {
    const user = userEvent.setup();
    renderModal(suite({ supports: { target: false, unit_serial: true } }));
    await waitFor(() => expect(screen.getByLabelText("Duration (s)")).toHaveValue(300));
    await user.type(screen.getByLabelText("Unit serial"), "HC-042");
    await user.click(screen.getByRole("button", { name: "Start run" }));
    expect(startRun).toHaveBeenCalledWith(expect.objectContaining({ unit_serial: "HC-042" }));
  });

  it("asks for no units when the suite records no serial", async () => {
    renderModal(suite({ supports: { target: true, unit_serial: false } }));
    await waitFor(() => expect(screen.getByLabelText("Duration (s)")).toHaveValue(300));
    expect(listUnits).not.toHaveBeenCalled();
  });

  it("fills each override with the value the selected profile gives it", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByLabelText("Duration (s)")).toHaveValue(300));
    expect(screen.getByLabelText("Cycles")).toHaveValue(12);
    expect(screen.getByLabelText("Stop on failure")).toBeChecked();
  });

  it("falls back to the manifest default for a field the profile omits", async () => {
    getProfile.mockResolvedValue({ body: "cycles: 12\n", name: "mock.yaml", path: "/p/mock.yaml" });
    renderModal();
    await waitFor(() => expect(screen.getByLabelText("Cycles")).toHaveValue(12));
    expect(screen.getByLabelText("Duration (s)")).toHaveValue(60);
    expect(screen.getByLabelText("Stop on failure")).not.toBeChecked();
  });

  it("refills the overrides when the operator picks another profile", async () => {
    const user = userEvent.setup();
    renderModal();
    await waitFor(() => expect(screen.getByLabelText("Duration (s)")).toHaveValue(300));
    getProfile.mockResolvedValue({
      body: "duration_s: 7200\n",
      name: "long.yaml",
      path: "/p/long.yaml",
    });
    await user.selectOptions(screen.getByLabelText("Profile"), "long.yaml");
    await waitFor(() => expect(screen.getByLabelText("Duration (s)")).toHaveValue(7200));
  });

  it("summarises the argv the choices produce", async () => {
    const user = userEvent.setup();
    renderModal();
    await waitFor(() => expect(screen.getByLabelText("Duration (s)")).toHaveValue(300));
    await user.clear(screen.getByLabelText("Duration (s)"));
    await user.type(screen.getByLabelText("Duration (s)"), "90");
    expect(screen.getByText("--duration-s 90 --cycles 12 --stop-on-failure")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Stop on failure"));
    expect(screen.getByText("--duration-s 90 --cycles 12")).toBeInTheDocument();
  });

  it("blocks the run while an override is invalid", async () => {
    const user = userEvent.setup();
    renderModal();
    await waitFor(() => expect(screen.getByLabelText("Cycles")).toHaveValue(12));
    await user.type(screen.getByLabelText("Cycles"), ".5");
    expect(screen.getByRole("button", { name: "Start run" })).toBeDisabled();
    expect(startRun).not.toHaveBeenCalled();
  });

  it("posts the declared overrides and navigates to the run", async () => {
    const user = userEvent.setup();
    renderModal();
    await waitFor(() => expect(screen.getByLabelText("Duration (s)")).toHaveValue(300));
    await user.type(screen.getByLabelText("Target"), "192.168.55.1");
    await user.click(screen.getByRole("button", { name: "Start run" }));
    expect(startRun).toHaveBeenCalledWith({
      overrides: { cycles: 12, duration_s: 300, stop_on_failure: true },
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

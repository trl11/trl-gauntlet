import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Instrument, Suite } from "@api/types";

import TestsPage from "./TestsPage";
import { pending, spinners } from "../test/queries";

const listInstruments = vi.fn();
const listSuites = vi.fn();
const rescanSuites = vi.fn();
const verifySuite = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    listInstruments: () => listInstruments(),
    listSuites: () => listSuites(),
    rescanSuites: () => rescanSuites(),
    verifySuite: (...args: unknown[]) => verifySuite(...args),
  };
});

function suite(partial: Partial<Suite> & { key: string; title: string }): Suite {
  return {
    apiVersion: 1,
    category: "hardware",
    conformance_profile: "",
    default_metrics: [],
    description: "",
    directory: `/suites/${partial.key}`,
    exec: {
      args: {},
      command: ["python", "-m", partial.key],
      env: {},
      graceful_stop_signal: "SIGUSR1",
      profile_schema_command: [],
      workdir: ".",
    },
    overrides: [],
    produces: ["verdict"],
    profiles: "./profiles",
    profiles_available: [],
    requires: [],
    supports: { target: true, unit_serial: false },
    ...partial,
  };
}

const THERMAL = suite({
  category: "hardware",
  description: "Chamber profile with per-segment pass/fail.",
  key: "thermal_cycle",
  produces: ["metrics", "verdict"],
  profiles_available: [
    { description: "quick", name: "mock.yaml", path: "/p/mock.yaml", user_authored: false },
  ],
  requires: ["chamber"],
  title: "Thermal Cycle",
});

const SMOKE = suite({ category: "software", key: "smoke", title: "Smoke" });

function instrument(name: string, available: boolean): Instrument {
  return {
    available,
    commands: [],
    description: "",
    instance_id: `${name}-0`,
    kind: name,
    name,
    unavailable_reason: "",
    state: {},
  };
}

function renderPage(path = "/tests") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/tests" element={<TestsPage />} />
          <Route path="/runs/:runId" element={<p>run page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  listSuites.mockResolvedValue({ errors: [], suites: [SMOKE, THERMAL] });
  listInstruments.mockResolvedValue({ instruments: [instrument("chamber", true)] });
  rescanSuites.mockResolvedValue({ count: 2, errors: [] });
  verifySuite.mockResolvedValue({
    checks: [{ detail: "manifest parses", fatal: true, name: "manifest", passed: true }],
    directory: "/suites/thermal_cycle",
    executed: false,
    passed: true,
    run_dir: "",
    suite: "thermal_cycle",
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("TestsPage", () => {
  it("groups the catalog by category", async () => {
    renderPage();
    expect(await screen.findByText("hardware")).toBeInTheDocument();
    expect(screen.getByText("software")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Thermal Cycle" })).toBeInTheDocument();
  });

  it("selects the suite named in the query string", async () => {
    renderPage("/tests?suite=thermal_cycle");
    expect(await screen.findByRole("region", { name: "Thermal Cycle" })).toBeInTheDocument();
    expect(screen.getByText("Chamber profile with per-segment pass/fail.")).toBeInTheDocument();
  });

  it("filters the rail by the search box", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(await screen.findByLabelText("Search"), "smoke");
    expect(screen.queryByRole("button", { name: "Thermal Cycle" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Smoke" })).toBeInTheDocument();
  });

  it("keeps what a suite produces and requires out of the detail pane", async () => {
    renderPage("/tests?suite=thermal_cycle");
    expect(await screen.findByRole("button", { name: "Start run" })).toBeInTheDocument();
    expect(screen.queryByText("metrics")).not.toBeInTheDocument();
    expect(screen.queryByText("chamber")).not.toBeInTheDocument();
  });

  it("refuses to start a suite whose requirement is unavailable, and says why", async () => {
    listInstruments.mockResolvedValue({ instruments: [instrument("chamber", false)] });
    renderPage("/tests?suite=thermal_cycle");
    await waitFor(() => expect(screen.getByRole("button", { name: "Start run" })).toBeDisabled());
    expect(screen.getByRole("status")).toHaveTextContent("chamber is unavailable");
  });

  it("rescans the suite roots and verifies every suite it found", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Rescan" }));
    await waitFor(() => expect(rescanSuites).toHaveBeenCalled());
    await waitFor(() => expect(verifySuite).toHaveBeenCalledTimes(2));
    expect(verifySuite).toHaveBeenCalledWith("smoke");
    expect(verifySuite).toHaveBeenCalledWith("thermal_cycle");
  });

  it("lists only the checks a suite failed", async () => {
    verifySuite.mockResolvedValue({
      checks: [
        { detail: "manifest parses", fatal: true, name: "manifest", passed: true },
        { detail: "no verdict.json declared", fatal: false, name: "produces", passed: false },
      ],
      directory: "/suites/thermal_cycle",
      executed: false,
      passed: false,
      run_dir: "",
      suite: "thermal_cycle",
    });
    const user = userEvent.setup();
    renderPage("/tests?suite=thermal_cycle");
    await user.click(await screen.findByRole("button", { name: "Rescan" }));
    expect(await screen.findByText("Conformance failed: 1 of 2 checks")).toBeInTheDocument();
    expect(screen.getByText("produces")).toBeInTheDocument();
    expect(screen.queryByText("manifest")).not.toBeInTheDocument();
  });

  it("says nothing about conformance for a suite that passed every check", async () => {
    const user = userEvent.setup();
    renderPage("/tests?suite=thermal_cycle");
    await user.click(await screen.findByRole("button", { name: "Rescan" }));
    await waitFor(() => expect(verifySuite).toHaveBeenCalled());
    expect(screen.queryByText(/Conformance/)).not.toBeInTheDocument();
  });

  it("reports a rescan that failed", async () => {
    rescanSuites.mockRejectedValue(new Error("suite roots unreadable"));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Rescan" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("suite roots unreadable");
  });

  it("carries the picked profile into the run dialog", async () => {
    const user = userEvent.setup();
    renderPage("/tests?suite=thermal_cycle");
    await user.click(await screen.findByRole("button", { name: /mock\.yaml/ }));
    await user.click(screen.getByRole("button", { name: "Start run" }));
    expect(await screen.findByLabelText("Profile")).toHaveValue("mock.yaml");
  });

  it("offers a rescan when nothing was discovered", async () => {
    listSuites.mockResolvedValue({ errors: [], suites: [] });
    renderPage();
    expect(await screen.findByText("No suites discovered")).toBeInTheDocument();
  });

  it("spins while the catalog is being read", () => {
    listSuites.mockReturnValue(pending());
    renderPage();
    expect(spinners()).toHaveLength(1);
    expect(screen.queryByText("No suites discovered")).not.toBeInTheDocument();
  });

  it("reports a catalog that could not be read", async () => {
    listSuites.mockRejectedValue(new Error("suite roots unreadable"));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("suite roots unreadable");
  });

  it("surfaces the discovery errors the catalog collected", async () => {
    listSuites.mockResolvedValue({ errors: ["/suites/bad/suite.yaml: missing key"], suites: [] });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("missing key");
  });
});

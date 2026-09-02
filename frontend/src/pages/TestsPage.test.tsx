import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Campaign, CampaignMember, Instrument, Suite } from "@api/types";

import TestsPage from "./TestsPage";
import { pending, spinners } from "../test/queries";

const listInstruments = vi.fn();
const listSuites = vi.fn();
const rescanSuites = vi.fn();
const listCampaigns = vi.fn();
const rescanCampaigns = vi.fn();
const getCampaign = vi.fn();
const verifySuite = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    listInstruments: () => listInstruments(),
    listSuites: () => listSuites(),
    rescanSuites: () => rescanSuites(),
    listCampaigns: () => listCampaigns(),
    rescanCampaigns: () => rescanCampaigns(),
    getCampaign: (...args: unknown[]) => getCampaign(...args),
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
    setup: "",
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
    {
      description: "quick",
      label: "Mock",
      name: "mock.yaml",
      path: "/p/mock.yaml",
      user_authored: false,
    },
  ],
  requires: ["chamber"],
  setup: "Chamber on the bench.\n\n  +------+\n  | oven |\n  +------+",
  title: "Thermal Cycle",
});

const SMOKE = suite({ category: "software", key: "smoke", title: "Smoke" });

const BENCH: Campaign = {
  apiVersion: 1,
  description: "Suites that drive real hardware.",
  directory: "/campaigns/bench",
  key: "bench",
  member_count: 1,
  suites: "./suites",
  suites_dir: "/campaigns/bench/suites",
  title: "Hardware Bench",
};

const MEMBER: CampaignMember = {
  component: "LAN7430-I/Y9X",
  declared: true,
  fixture: "1-1",
  host: "Raspberry Pi",
  notes: "",
  overrides: {},
  present: true,
  profile: "smoke.yaml",
  suite: "thermal_cycle",
  target: "",
  test_vehicle: "EVB",
  title: "Thermal Cycle",
  unit_serial: "",
};

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
  rescanSuites.mockResolvedValue({ errors: [], suites: [SMOKE, THERMAL] });
  listCampaigns.mockResolvedValue({ campaigns: [BENCH], errors: [] });
  rescanCampaigns.mockResolvedValue({ campaigns: [BENCH], errors: [] });
  getCampaign.mockResolvedValue({ ...BENCH, members: [MEMBER] });
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

  it("shows the setup of the selected suite with its line breaks intact", async () => {
    renderPage("/tests?suite=thermal_cycle");
    const detail = await screen.findByRole("region", { name: "Thermal Cycle" });
    expect(detail.querySelector("pre")?.textContent).toBe(THERMAL.setup);
  });

  it("lists every discovered suite, with no search box to filter them", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "Thermal Cycle" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Smoke" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Search")).not.toBeInTheDocument();
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

  it("takes the catalog from the rescan rather than reading the list again", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("button", { name: "Smoke" });
    listSuites.mockClear();
    await user.click(screen.getByRole("button", { name: "Rescan" }));
    await waitFor(() => expect(verifySuite).toHaveBeenCalledTimes(2));
    expect(listSuites).not.toHaveBeenCalled();
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
    await user.click(await screen.findByRole("button", { name: /Mock/ }));
    await user.click(screen.getByRole("button", { name: "Start run" }));
    expect(await screen.findByLabelText("Profile")).toHaveValue("mock.yaml");
  });

  it("marks the picked profile on the whole row, not just its name", async () => {
    const user = userEvent.setup();
    const { container } = renderPage("/tests?suite=thermal_cycle");
    const pick = await screen.findByRole("button", { name: /Mock/ });
    expect(container.querySelector(".suite-detail__profile--active")).toBeNull();

    await user.click(pick);

    // The row carries the tint and the edge; a colour on the name alone is
    // what this replaced, and was too easy to miss.
    expect(container.querySelector(".suite-detail__profile--active")).not.toBeNull();
    expect(pick).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Selected")).toBeInTheDocument();
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

describe("TestsPage campaign view", () => {
  it("shows the suite rail until the campaign view is chosen", async () => {
    renderPage();

    expect(await screen.findByRole("button", { name: "Thermal Cycle" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Hardware Bench" })).not.toBeInTheDocument();
  });

  it("switches to campaigns and back without leaving the page", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("button", { name: "Thermal Cycle" });

    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    expect(await screen.findByRole("region", { name: "Hardware Bench" })).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "All tests" }));
    expect(await screen.findByRole("button", { name: "Thermal Cycle" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Hardware Bench" })).not.toBeInTheDocument();
  });

  it("opens on the campaign view when the query string asks for it", async () => {
    renderPage("/tests?view=campaigns");

    expect(await screen.findByRole("region", { name: "Hardware Bench" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Campaigns" })).toHaveAttribute("aria-selected", "true");
  });

  it("does not fetch campaigns until the view needs them", async () => {
    renderPage();
    await screen.findByRole("button", { name: "Thermal Cycle" });

    expect(listCampaigns).not.toHaveBeenCalled();
  });

  it("rescans campaigns rather than suites while showing them", async () => {
    const user = userEvent.setup();
    renderPage("/tests?view=campaigns");
    await screen.findByRole("region", { name: "Hardware Bench" });

    await user.click(screen.getByRole("button", { name: /Rescan/ }));

    await waitFor(() => expect(rescanCampaigns).toHaveBeenCalled());
    expect(rescanSuites).not.toHaveBeenCalled();
  });

  it("reports a campaign discovery error without hiding the rest", async () => {
    listCampaigns.mockResolvedValue({
      campaigns: [BENCH],
      errors: ["campaign.yaml: apiVersion 99 is not supported"],
    });
    renderPage("/tests?view=campaigns");

    expect(await screen.findByRole("alert")).toHaveTextContent("apiVersion 99");
    expect(await screen.findByRole("region", { name: "Hardware Bench" })).toBeInTheDocument();
  });

  it("says so when no campaign was discovered", async () => {
    listCampaigns.mockResolvedValue({ campaigns: [], errors: [] });
    renderPage("/tests?view=campaigns");

    expect(await screen.findByText("No campaigns discovered")).toBeInTheDocument();
  });
});

describe("TestsPage remembered view", () => {
  it("returns to the campaign view on a visit that names no parameters", async () => {
    const user = userEvent.setup();
    const first = renderPage();
    await screen.findByRole("button", { name: "Thermal Cycle" });
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    await screen.findByRole("region", { name: "Hardware Bench" });
    first.unmount();

    renderPage();

    expect(await screen.findByRole("region", { name: "Hardware Bench" })).toBeInTheDocument();
  });

  it("returns to the campaign that was open", async () => {
    const first = renderPage("/tests?view=campaigns&campaign=bench");
    await screen.findByRole("region", { name: "Hardware Bench" });
    first.unmount();

    renderPage();

    await screen.findByRole("region", { name: "Hardware Bench" });
    expect(getCampaign).toHaveBeenLastCalledWith("bench");
  });

  it("honours a link that names a view over what was remembered", async () => {
    const user = userEvent.setup();
    const first = renderPage();
    await screen.findByRole("button", { name: "Thermal Cycle" });
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    await screen.findByRole("region", { name: "Hardware Bench" });
    first.unmount();

    renderPage("/tests?view=suites");

    expect(await screen.findByRole("button", { name: "Thermal Cycle" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Hardware Bench" })).not.toBeInTheDocument();
  });

  it("forgets the campaign view once the operator goes back to all tests", async () => {
    const user = userEvent.setup();
    const first = renderPage();
    await screen.findByRole("button", { name: "Thermal Cycle" });
    await user.click(screen.getByRole("tab", { name: "Campaigns" }));
    await screen.findByRole("region", { name: "Hardware Bench" });
    await user.click(screen.getByRole("tab", { name: "All tests" }));
    await screen.findByRole("button", { name: "Thermal Cycle" });
    first.unmount();

    renderPage();

    expect(await screen.findByRole("button", { name: "Thermal Cycle" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Hardware Bench" })).not.toBeInTheDocument();
  });

  it("works when storage is unavailable", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    renderPage("/tests?view=campaigns");

    expect(await screen.findByRole("region", { name: "Hardware Bench" })).toBeInTheDocument();
    getItem.mockRestore();
    setItem.mockRestore();
  });
});

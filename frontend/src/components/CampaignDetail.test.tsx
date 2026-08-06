import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Campaign, CampaignMember, RunRow } from "@api/types";

import CampaignDetail from "./CampaignDetail";

const getCampaign = vi.fn();
const runCampaignMember = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getCampaign: (...args: unknown[]) => getCampaign(...args),
    runCampaignMember: (...args: unknown[]) => runCampaignMember(...args),
  };
});

function member(partial: Partial<CampaignMember> & { suite: string }): CampaignMember {
  return {
    component: "",
    declared: true,
    fixture: "",
    host: "",
    notes: "",
    overrides: {},
    present: true,
    profile: "",
    target: "",
    test_vehicle: "",
    title: partial.suite,
    unit_serial: "",
    ...partial,
  };
}

const LAST_RUN: RunRow = {
  duration_s: 12,
  ended_at: "2026-08-01T00:01:00Z",
  fail_reason: null,
  profile: "smoke.yaml",
  run_dir: "/runs/ssd/RUN-9",
  run_id: "RUN-9",
  started_at: "2026-08-01T00:00:00Z",
  status: "passed",
  suite: "ssd",
  target: null,
  unit_serial: null,
  verdict: "PASS",
};

const BENCH: Campaign = {
  apiVersion: 1,
  description: "Suites that drive real hardware.",
  directory: "/campaigns/bench",
  key: "bench",
  member_count: 2,
  suites: "./suites",
  suites_dir: "/campaigns/bench/suites",
  title: "Hardware Bench",
};

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tests"]}>
        <Routes>
          <Route path="/tests" element={<CampaignDetail campaignKey="bench" />} />
          <Route path="/runs/:runId" element={<p>run page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getCampaign.mockResolvedValue({
    ...BENCH,
    members: [
      member({
        component: "LAN7430-I/Y9X",
        fixture: "1-1",
        profile: "smoke.yaml",
        suite: "ssd",
        title: "SSD Endurance",
      }),
    ],
  });
  runCampaignMember.mockResolvedValue({ ...LAST_RUN, run_id: "RUN-10" });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("CampaignDetail", () => {
  it("lists each member with what the campaign declares for it", async () => {
    renderDetail();

    const row = await screen.findByRole("row", { name: /SSD Endurance/ });
    expect(within(row).getByText("LAN7430-I/Y9X")).toBeInTheDocument();
    expect(within(row).getByText("1-1")).toBeInTheDocument();
    expect(within(row).getByText("smoke.yaml")).toBeInTheDocument();
  });

  it("shows no run history, which belongs to the run", async () => {
    renderDetail();

    await screen.findByRole("row", { name: /SSD Endurance/ });
    expect(screen.queryByRole("columnheader", { name: "Runs" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Last run" })).not.toBeInTheDocument();
  });

  it("marks a member the manifest does not declare", async () => {
    getCampaign.mockResolvedValue({
      ...BENCH,
      members: [member({ declared: false, suite: "rs422", title: "RS422 Link" })],
    });
    renderDetail();

    expect(await screen.findByText("undeclared")).toBeInTheDocument();
  });

  it("marks a declared member whose suite is missing and will not run it", async () => {
    getCampaign.mockResolvedValue({
      ...BENCH,
      members: [member({ present: false, suite: "gone", title: "Gone" })],
    });
    renderDetail();

    expect(await screen.findByText("not on disk")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run/ })).toBeDisabled();
  });

  it("runs a member and follows the run it started", async () => {
    const user = userEvent.setup();
    renderDetail();
    await screen.findByRole("row", { name: /SSD Endurance/ });

    await user.click(screen.getByRole("button", { name: /Run/ }));

    await waitFor(() => expect(runCampaignMember).toHaveBeenCalledWith("bench", "ssd"));
    expect(await screen.findByText("run page")).toBeInTheDocument();
  });

  it("reports a refused run rather than navigating", async () => {
    const user = userEvent.setup();
    runCampaignMember.mockRejectedValue(new Error("a run is already in progress"));
    renderDetail();
    await screen.findByRole("row", { name: /SSD Endurance/ });

    await user.click(screen.getByRole("button", { name: /Run/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already in progress");
    expect(screen.queryByText("run page")).not.toBeInTheDocument();
  });

  it("offers no manifest editing, the file on disk being the source of truth", async () => {
    renderDetail();

    await screen.findByRole("row", { name: /SSD Endurance/ });
    expect(screen.queryByRole("button", { name: "Edit manifest" })).not.toBeInTheDocument();
  });

  it("says so when the campaign has no members yet", async () => {
    getCampaign.mockResolvedValue({ ...BENCH, members: [] });
    renderDetail();

    expect(await screen.findByText("No tests in this campaign")).toBeInTheDocument();
  });
});

describe("CampaignDetail optional columns", () => {
  it("leaves out a column no member fills", async () => {
    getCampaign.mockResolvedValue({
      ...BENCH,
      members: [member({ profile: "smoke.yaml", suite: "rs422", title: "RS422 Link" })],
    });
    renderDetail();

    await screen.findByRole("row", { name: /RS422 Link/ });
    expect(screen.queryByRole("columnheader", { name: "Component" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Fixture" })).not.toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Profile" })).toBeInTheDocument();
  });

  it("shows a column as soon as one member fills it", async () => {
    getCampaign.mockResolvedValue({
      ...BENCH,
      members: [
        member({ suite: "rs422", title: "RS422 Link" }),
        member({ fixture: "1-1", suite: "ssd", title: "SSD Endurance" }),
      ],
    });
    renderDetail();

    expect(await screen.findByRole("columnheader", { name: "Fixture" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Component" })).not.toBeInTheDocument();
  });
});

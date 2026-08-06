import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunRow } from "@api/types";

import Layout from "./Layout";

const getSystemInfo = vi.fn();
const listRuns = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getSystemInfo: () => getSystemInfo(),
    listRuns: (...args: unknown[]) => listRuns(...args),
  };
});

function liveRun(): RunRow {
  return {
    run_id: "20260101T000000Z-0001",
    suite: "thermal_cycle",
    status: "running",
    started_at: "2026-01-01T00:00:00Z",
    ended_at: null,
    duration_s: null,
    verdict: null,
    fail_reason: null,
    profile: "mock.yaml",
    target: null,
    unit_serial: null,
    run_dir: "/runs/thermal_cycle/20260101T000000Z-0001",
  };
}

function renderLayout(initial = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<p>dashboard body</p>} />
            <Route path="tests" element={<p>tests body</p>} />
            <Route path="runs/:runId" element={<p>run body</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getSystemInfo.mockResolvedValue({
    gauntlet: "0.4.1",
    gauntlet_sdk: "0.4.1",
    contract_version: 1,
    python: "3.12.3",
    os: "Linux",
  });
  listRuns.mockResolvedValue({ runs: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Layout", () => {
  it("renders the wordmark and every nav link", async () => {
    renderLayout();
    expect(screen.getByRole("link", { name: "Gauntlet" })).toHaveAttribute("href", "/");
    for (const label of ["Dashboard", "History", "Tests", "Units", "Instruments", "Settings"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  });

  it("points each nav link at its route", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: "History" })).toHaveAttribute("href", "/history");
    expect(screen.getByRole("link", { name: "Units" })).toHaveAttribute("href", "/units");
  });

  it("marks the current route as the active link", () => {
    const { container } = renderLayout("/tests");
    const active = container.querySelector(".layout__tab.is-active");
    expect(active).toHaveTextContent("Tests");
    expect(active).toHaveAttribute("aria-current", "page");
  });

  it("keeps History active on a run view, whatever page reached it", () => {
    const { container } = renderLayout("/runs/20260101T000000Z-0001");
    const active = container.querySelectorAll(".layout__tab.is-active");
    expect(active).toHaveLength(1);
    expect(active[0]).toHaveTextContent("History");
  });

  it("leaves the dashboard tab inactive on every other route", () => {
    const { container } = renderLayout("/tests");
    const dashboard = screen.getByRole("link", { name: "Dashboard" });
    expect(dashboard).not.toHaveClass("is-active");
    expect(container.querySelectorAll(".layout__tab.is-active")).toHaveLength(1);
  });

  it("renders the routed page through the outlet", () => {
    renderLayout();
    expect(screen.getByText("dashboard body")).toBeInTheDocument();
  });

  it("offers a skip link ahead of the navigation", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("shows the version once /api/version answers", async () => {
    renderLayout();
    expect(await screen.findByText("v0.4.1")).toBeInTheDocument();
  });

  it("hides the active-run indicator when nothing is in flight", () => {
    const { container } = renderLayout();
    expect(container.querySelector(".layout__active-run")).toBeNull();
  });

  it("links to the run in flight", async () => {
    listRuns.mockResolvedValue({ runs: [liveRun()] });
    renderLayout();
    const link = await screen.findByRole("link", { name: /thermal_cycle/ });
    expect(link).toHaveAttribute("href", "/runs/20260101T000000Z-0001");
  });

  it("routes the run-a-test button to /tests", async () => {
    renderLayout();
    await userEvent.click(screen.getByRole("button", { name: /run a test/i }));
    expect(screen.getByText("tests body")).toBeInTheDocument();
  });

  it("toggles the collapsed tab list", async () => {
    const { container } = renderLayout();
    const toggle = screen.getByRole("button", { name: "Open navigation" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "primary-tabs");

    await userEvent.click(toggle);
    await waitFor(() => expect(container.querySelector(".layout__tabs")).toHaveClass("is-open"));
    expect(screen.getByRole("button", { name: "Close navigation" })).toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunRow, UnitDetail as UnitDetailRow } from "@api/types";

import UnitDetail from "./UnitDetail";

const addUnitNote = vi.fn();
const deleteUnitNote = vi.fn();
const getUnit = vi.fn();
const getUnitHistory = vi.fn();
const listUnitNotes = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    addUnitNote: (...args: unknown[]) => addUnitNote(...args),
    deleteUnitNote: (...args: unknown[]) => deleteUnitNote(...args),
    getUnit: (...args: unknown[]) => getUnit(...args),
    getUnitHistory: (...args: unknown[]) => getUnitHistory(...args),
    listUnitNotes: (...args: unknown[]) => listUnitNotes(...args),
  };
});

function unit(): UnitDetailRow {
  return {
    failed: 1,
    first_seen: "2026-01-01T00:00:00Z",
    last_run: null,
    last_seen: "2026-01-03T00:00:00Z",
    note_count: 1,
    notes: [],
    passed: 3,
    run_count: 4,
    serial: "HC-001",
  };
}

function run(runId: string, status: RunRow["status"]): RunRow {
  return {
    duration_s: 12,
    ended_at: "2026-01-01T00:01:00Z",
    fail_reason: null,
    profile: "mock.yaml",
    run_dir: `/runs/${runId}`,
    run_id: runId,
    started_at: "2026-01-01T00:00:00Z",
    status,
    suite: "thermal_cycle",
    target: null,
    unit_serial: "HC-001",
    verdict: status === "passed" ? "PASS" : "FAIL",
  };
}

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <UnitDetail serial="HC-001" />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getUnit.mockResolvedValue(unit());
  getUnitHistory.mockResolvedValue({ runs: [run("r-1", "passed"), run("r-2", "failed")] });
  listUnitNotes.mockResolvedValue({
    notes: [{ id: 1, body: "cracked lid", author: "gabe", created_at: "2026-01-02T00:00:00Z" }],
  });
  addUnitNote.mockResolvedValue({ id: 2, body: "x", author: null, created_at: "" });
  deleteUnitNote.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("UnitDetail", () => {
  it("shows the serial and a way back to the list", async () => {
    renderDetail();
    expect(screen.getByRole("heading", { name: "HC-001" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /all units/i })).toHaveAttribute("href", "/units");
  });

  it("reports the counters once the unit loads", async () => {
    const { container } = renderDetail();
    expect(await screen.findByText("75%")).toBeInTheDocument();
    const facts = container.querySelector(".unit-detail__facts") as HTMLElement;
    expect(within(facts).getByText("Passed")).toBeInTheDocument();
    expect(within(facts).getByText("4")).toBeInTheDocument();
  });

  it("lists the unit's runs", async () => {
    renderDetail();
    expect(await screen.findByText("r-1")).toBeInTheDocument();
    expect(screen.getByText("r-2")).toBeInTheDocument();
  });

  it("charts the pass rate once there are runs", async () => {
    const { container } = renderDetail();
    await screen.findByText("r-1");
    expect(container.querySelector(".unit-detail__chart")).not.toBeNull();
  });

  it("shows the notes and posts a new one", async () => {
    renderDetail();
    expect(await screen.findByText("cracked lid")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Add a note"), "swapped harness");
    await userEvent.click(screen.getByRole("button", { name: "Add note" }));
    expect(addUnitNote).toHaveBeenCalledWith("HC-001", "swapped harness", null);
  });

  it("reports a unit the API does not know", async () => {
    getUnit.mockRejectedValue(new Error("unknown unit 'HC-001'"));
    renderDetail();
    expect(await screen.findByText("Unknown unit")).toBeInTheDocument();
  });
});

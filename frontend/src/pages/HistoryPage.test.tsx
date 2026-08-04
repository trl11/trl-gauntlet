import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunRow, Verdict } from "@api/types";

import HistoryPage from "./HistoryPage";
import { pending } from "../test/queries";

const deleteRun = vi.fn();
const getRunVerdict = vi.fn();
const listRuns = vi.fn();
const listSuites = vi.fn();
const listUnits = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    deleteRun: (...args: unknown[]) => deleteRun(...args),
    getRunVerdict: (...args: unknown[]) => getRunVerdict(...args),
    listRuns: (...args: unknown[]) => listRuns(...args),
    listSuites: () => listSuites(),
    listUnits: () => listUnits(),
  };
});

function run(overrides: Partial<RunRow> = {}): RunRow {
  return {
    duration_s: 61,
    ended_at: "2026-02-01T10:01:01Z",
    fail_reason: null,
    profile: "mock.yaml",
    run_dir: "/runs/thermal_cycle/r1",
    run_id: "r1",
    started_at: "2026-02-01T10:00:00Z",
    status: "passed",
    suite: "thermal_cycle",
    target: null,
    unit_serial: "HC-001",
    verdict: "PASS",
    ...overrides,
  };
}

function verdict(): Verdict {
  return {
    abort_reason: "",
    aborted: false,
    duration_s: 61,
    ended_at_utc: "2026-02-01T10:01:01Z",
    failures: 1,
    passed: false,
    reason: "drift out of band",
    results: [],
    started_at_utc: "2026-02-01T10:00:00Z",
    stopped_early: false,
    successes: 4,
    tests: [],
    total_iterations: 5,
  };
}

/** jsdom's Blob has no `text()`, so read it the long way. */
function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(blob);
  });
}

function renderHistory(url = "/history") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <HistoryPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  deleteRun.mockResolvedValue(undefined);
  getRunVerdict.mockResolvedValue(verdict());
  listRuns.mockResolvedValue({
    runs: [run(), run({ run_id: "r2", started_at: "2026-02-02T10:00:00Z", status: "failed" })],
    total: 2,
  });
  listSuites.mockResolvedValue({ errors: [], suites: [] });
  listUnits.mockResolvedValue({ units: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("HistoryPage", () => {
  it("asks the server for one page of runs", async () => {
    renderHistory();
    await waitFor(() =>
      expect(listRuns).toHaveBeenCalledWith({
        after: null,
        before: null,
        direction: "desc",
        limit: 20,
        offset: 0,
        sort: "started_at",
        status: [],
        suite: null,
        unit_serial: null,
      })
    );
  });

  it("takes the filters and paging out of the URL", async () => {
    renderHistory(
      "/history?suite=thermal_cycle&unit=HC-001&page=2&size=50&after=2026-02-01&before=2026-02-28"
    );
    await waitFor(() =>
      expect(listRuns).toHaveBeenCalledWith({
        after: "2026-02-01",
        before: "2026-02-28",
        direction: "desc",
        limit: 50,
        offset: 50,
        sort: "started_at",
        status: [],
        suite: "thermal_cycle",
        unit_serial: "HC-001",
      })
    );
  });

  it("asks for every in-flight status behind one filter value", async () => {
    renderHistory("/history?status=live");
    await waitFor(() =>
      expect(listRuns).toHaveBeenCalledWith(
        expect.objectContaining({ status: ["aborting", "running", "starting", "stopping"] })
      )
    );
  });

  it("passes a plain status straight through", async () => {
    renderHistory("/history?status=failed");
    await waitFor(() =>
      expect(listRuns).toHaveBeenCalledWith(expect.objectContaining({ status: ["failed"] }))
    );
  });

  it("links a row's unit cell to that unit", async () => {
    renderHistory();
    const links = await screen.findAllByRole("link", { name: "HC-001" });
    expect(links[0]).toHaveAttribute("href", "/units/HC-001");
  });

  it("reports the server's total, not the page length", async () => {
    listRuns.mockResolvedValue({ runs: [run()], total: 137 });
    renderHistory();
    expect(await screen.findByText(/1 of 137 · page 1 of 7/)).toBeInTheDocument();
  });

  it("marks the table as loading while the page is being read", () => {
    listRuns.mockReturnValue(pending());
    renderHistory();
    expect(screen.queryByText("Nothing matches these filters.")).not.toBeInTheDocument();
  });

  it("reports a page of history that could not be read", async () => {
    listRuns.mockRejectedValue(new Error("runs index locked"));
    renderHistory();
    expect(await screen.findByText("runs index locked")).toBeInTheDocument();
  });

  it("shows an empty state when the server returns nothing", async () => {
    listRuns.mockResolvedValue({ runs: [], total: 0 });
    renderHistory();
    expect(await screen.findByText("Nothing to show")).toBeInTheDocument();
  });

  it("expands a row onto its verdict summary", async () => {
    renderHistory();
    await userEvent.click(await screen.findByRole("button", { name: "Details for run r1" }));
    expect(await screen.findByText("4 passed, 1 failed, 5 iterations")).toBeInTheDocument();
    expect(screen.getByText("drift out of band")).toBeInTheDocument();
  });

  it("records a sort in the URL and asks the server to apply it", async () => {
    renderHistory();
    await userEvent.click(await screen.findByRole("button", { name: "Suite" }));
    expect(screen.getByRole("columnheader", { name: /Suite/ })).toHaveAttribute(
      "aria-sort",
      "ascending"
    );
    await waitFor(() =>
      expect(listRuns).toHaveBeenCalledWith(
        expect.objectContaining({ direction: "asc", sort: "suite" })
      )
    );
  });

  it("exports the selected rows as CSV", async () => {
    const blobs: Blob[] = [];
    URL.createObjectURL = vi.fn((blob: Blob) => {
      blobs.push(blob);
      return "blob:csv";
    });
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    renderHistory();
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Select every run on this page" })
    );
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    expect(blobs).toHaveLength(1);
    const text = await readBlob(blobs[0]);
    const lines = text.split("\n");
    expect(lines[0]).toBe(
      "run_id,suite,profile,status,verdict,unit_serial,target,started_at,ended_at,duration_s,fail_reason"
    );
    expect(lines).toHaveLength(3);
    expect(lines[2]).toContain("r2,thermal_cycle,mock.yaml,failed");
  });

  it("disables the export until a row is selected", async () => {
    renderHistory();
    expect(await screen.findByRole("button", { name: "Export CSV" })).toBeDisabled();
  });

  it("deletes one run from its row menu, after confirming", async () => {
    renderHistory();
    await userEvent.click(await screen.findByRole("button", { name: "Actions for run r1" }));
    const menu = document.querySelector(".row-menu") as HTMLElement;
    await userEvent.click(within(menu).getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(deleteRun).toHaveBeenCalledWith("r1"));
  });

  it("batch-deletes every selected run, after confirming", async () => {
    renderHistory();
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Select every run on this page" })
    );
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(deleteRun).toHaveBeenCalledWith("r1"));
    expect(deleteRun).toHaveBeenCalledWith("r2");
  });

  it("reports a run that could not be deleted", async () => {
    deleteRun.mockRejectedValue(new Error("run is still in flight"));
    renderHistory();
    await userEvent.click(await screen.findByRole("button", { name: "Actions for run r1" }));
    const menu = document.querySelector(".row-menu") as HTMLElement;
    await userEvent.click(within(menu).getByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not delete 1 run");
  });
});

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { RunRow } from "@api/types";

import { COLUMNS, DEFAULT_RUN_COLUMNS, compare, matches, renderCell } from "./run_columns";

function run(partial: Partial<RunRow> = {}): RunRow {
  return {
    duration_s: 12.5,
    ended_at: "2026-01-01T00:00:12Z",
    fail_reason: null,
    profile: "quick.yaml",
    run_dir: "/output/runs/demo/r1",
    run_id: "r1",
    started_at: "2026-01-01T00:00:00Z",
    status: "passed",
    suite: "demo",
    target: null,
    unit_serial: "SN-1",
    verdict: "PASS",
    ...partial,
  };
}

function cell(column: Parameters<typeof renderCell>[1], row: RunRow = run()) {
  return render(<MemoryRouter>{renderCell(row, column)}</MemoryRouter>);
}

describe("column specs", () => {
  it("names every column the default order uses", () => {
    for (const column of DEFAULT_RUN_COLUMNS) {
      expect(COLUMNS[column].header).toBeTruthy();
    }
  });

  it("marks the reason column as unsortable", () => {
    expect(COLUMNS.fail_reason.sortable).toBe(false);
  });

  it("aligns duration to the right", () => {
    expect(COLUMNS.duration_s.align).toBe("right");
  });
});

describe("compare", () => {
  it("orders numbers numerically", () => {
    expect(compare(run({ duration_s: 2 }), run({ duration_s: 10 }), "duration_s")).toBeLessThan(0);
  });

  it("orders strings alphabetically", () => {
    expect(compare(run({ suite: "alpha" }), run({ suite: "beta" }), "suite")).toBeLessThan(0);
  });

  it("treats equal values as equal", () => {
    expect(compare(run(), run(), "run_id")).toBe(0);
  });

  it("sorts a null after a value whichever side it is on", () => {
    expect(compare(run({ target: null }), run({ target: "unit-3" }), "target")).toBeGreaterThan(0);
    expect(compare(run({ target: "unit-3" }), run({ target: null }), "target")).toBeLessThan(0);
  });

  it("sorts a whole column without throwing on nulls", () => {
    const rows = [run({ duration_s: null }), run({ duration_s: 5 }), run({ duration_s: 1 })];

    const sorted = [...rows].sort((a, b) => compare(a, b, "duration_s"));

    expect(sorted.map((row) => row.duration_s)).toEqual([1, 5, null]);
  });
});

describe("matches", () => {
  it("accepts every run when the term is empty", () => {
    expect(matches(run(), "")).toBe(true);
  });

  it("searches across the text fields", () => {
    expect(matches(run({ suite: "thermal_cycle" }), "thermal")).toBe(true);
    expect(matches(run({ unit_serial: "SN-42" }), "sn-42")).toBe(true);
    expect(matches(run({ fail_reason: "rail sagged" }), "sagged")).toBe(true);
  });

  it("rejects a term that appears nowhere", () => {
    expect(matches(run(), "nothing here")).toBe(false);
  });

  it("ignores null fields rather than matching the word null", () => {
    expect(matches(run({ target: null, fail_reason: null }), "null")).toBe(false);
  });
});

describe("renderCell", () => {
  it("renders the status as a pill", () => {
    cell("status");
    expect(screen.getByText("PASS")).toBeInTheDocument();
  });

  it("renders the run id in the monospace style", () => {
    const { container } = cell("run_id");
    expect(container.querySelector(".run-table__mono")?.textContent).toBe("r1");
  });

  it("formats the timestamp and the duration", () => {
    expect(cell("started_at").container.textContent).not.toBe("2026-01-01T00:00:00Z");
    expect(cell("duration_s").container.textContent).toContain("12");
  });

  it("shows a dash for a run that failed for no recorded reason", () => {
    expect(cell("fail_reason").container.textContent).toBe("-");
  });

  it("shows the reason when there is one", () => {
    expect(cell("fail_reason", run({ fail_reason: "rail sagged" })).container.textContent).toBe(
      "rail sagged"
    );
  });

  it("links a unit to its page", () => {
    cell("unit_serial", run({ unit_serial: "SN/1" }));
    expect(screen.getByRole("link", { name: "SN/1" })).toHaveAttribute("href", "/units/SN%2F1");
  });

  it("shows a dash for a run with no unit", () => {
    expect(cell("unit_serial", run({ unit_serial: null })).container.textContent).toBe("-");
  });

  it("falls back to the raw value for the remaining columns", () => {
    expect(cell("suite").container.textContent).toBe("demo");
    expect(cell("profile").container.textContent).toBe("quick.yaml");
  });

  it("shows a dash for an empty value in a fallback column", () => {
    expect(cell("target", run({ target: null })).container.textContent).toBe("-");
  });
});

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { RunRow } from "@api/types";

import RunTable, { type RunTableProps } from "./RunTable";

function run(partial: Partial<RunRow> & { run_id: string }): RunRow {
  return {
    duration_s: 12,
    ended_at: "2026-01-01T00:01:00Z",
    fail_reason: null,
    profile: "mock.yaml",
    run_dir: `/runs/${partial.run_id}`,
    started_at: "2026-01-01T00:00:00Z",
    status: "passed",
    suite: "thermal_cycle",
    target: null,
    unit_serial: "HC-001",
    verdict: "PASS",
    ...partial,
  };
}

const RUNS: RunRow[] = [
  run({ run_id: "r-1", started_at: "2026-01-01T00:00:00Z", suite: "smoke" }),
  run({ run_id: "r-2", started_at: "2026-01-02T00:00:00Z", status: "failed", verdict: "FAIL" }),
  run({ run_id: "r-3", started_at: "2026-01-03T00:00:00Z", profile: "long.yaml" }),
];

function renderTable(props: Partial<RunTableProps> = {}) {
  return render(
    <MemoryRouter initialEntries={["/history"]}>
      <Routes>
        <Route path="/history" element={<RunTable runs={RUNS} {...props} />} />
        <Route path="/runs/:runId" element={<p>run page</p>} />
      </Routes>
    </MemoryRouter>
  );
}

/** The run ids in the body, top to bottom. */
function listedIds(): string[] {
  return screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getByText(/^r-\d$/).textContent ?? "");
}

describe("RunTable", () => {
  it("lists every run it is given, newest first by default", () => {
    renderTable();
    expect(listedIds()).toEqual(["r-3", "r-2", "r-1"]);
  });

  it("says so when there is nothing to list", () => {
    renderTable({ runs: [], emptyMessage: "Nothing here." });
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
  });

  it("renders a skeleton rather than rows while loading", () => {
    const { container } = renderTable({ loading: true });
    expect(container.querySelector("tbody")?.querySelectorAll("tr").length).toBeGreaterThan(0);
    expect(screen.queryByText("r-1")).not.toBeInTheDocument();
  });

  it("opens a run by navigating to it", async () => {
    const user = userEvent.setup();
    renderTable({ columns: ["run_id", "status"] });
    await user.click(screen.getByRole("button", { name: "r-2" }));
    expect(await screen.findByText("run page")).toBeInTheDocument();
  });

  it("hands the run to onSelect instead, when the caller wants it", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderTable({ columns: ["run_id", "status"], onSelect });
    await user.click(screen.getByRole("button", { name: "r-2" }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ run_id: "r-2" }));
    expect(screen.queryByText("run page")).not.toBeInTheDocument();
  });

  it("keeps the row's own link out of the unit column, which has one of its own", () => {
    renderTable({ columns: ["unit_serial", "run_id"] });
    expect(screen.getByRole("button", { name: "r-2" })).toBeInTheDocument();
  });
});

describe("RunTable filtering", () => {
  it("filters on the run, the suite, the profile and the unit", async () => {
    const user = userEvent.setup();
    renderTable();
    const box = screen.getByLabelText("Filter runs");

    await user.type(box, "smoke");
    expect(listedIds()).toEqual(["r-1"]);

    await user.clear(box);
    await user.type(box, "long.yaml");
    expect(listedIds()).toEqual(["r-3"]);
  });

  it("filters by status", async () => {
    const user = userEvent.setup();
    renderTable();
    await user.selectOptions(screen.getByLabelText("Filter by status"), "failed");
    expect(listedIds()).toEqual(["r-2"]);
  });

  it("hides the filters when the caller filters for it", () => {
    renderTable({ filterable: false });
    expect(screen.queryByLabelText("Filter runs")).not.toBeInTheDocument();
  });
});

describe("RunTable sorting", () => {
  it("reverses the order when the active column is pressed again", async () => {
    const user = userEvent.setup();
    renderTable();
    await user.click(screen.getByRole("button", { name: /started/i }));
    expect(listedIds()).toEqual(["r-1", "r-2", "r-3"]);
  });

  it("sorts by another column ascending first", async () => {
    const user = userEvent.setup();
    renderTable();
    await user.click(screen.getByRole("button", { name: /^run/i }));
    expect(listedIds()).toEqual(["r-1", "r-2", "r-3"]);
  });

  it("reports the column and direction rather than reordering, when controlled", async () => {
    const user = userEvent.setup();
    const onSort = vi.fn();
    renderTable({ onSort, sort: "started_at", direction: "desc" });

    await user.click(screen.getByRole("button", { name: /started/i }));

    expect(onSort).toHaveBeenCalledWith("started_at", "asc");
    expect(listedIds()).toEqual(["r-1", "r-2", "r-3"]);
  });

  it("offers no sort control on a column that does not sort", () => {
    renderTable({ columns: ["status", "fail_reason"] });
    expect(screen.getByRole("columnheader", { name: "Reason" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reason/i })).not.toBeInTheDocument();
  });

  it("marks the sorted column for a screen reader", async () => {
    const user = userEvent.setup();
    renderTable();
    expect(screen.getByRole("columnheader", { name: /started/i })).toHaveAttribute(
      "aria-sort",
      "descending"
    );
    await user.click(screen.getByRole("button", { name: /started/i }));
    expect(screen.getByRole("columnheader", { name: /started/i })).toHaveAttribute(
      "aria-sort",
      "ascending"
    );
  });
});

describe("RunTable selection", () => {
  it("renders no checkbox column unless the caller tracks a selection", () => {
    renderTable();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("adds and removes one run from the selection", async () => {
    const user = userEvent.setup();
    const onSelectionChange = vi.fn();
    renderTable({ onSelectionChange, selectedIds: [] });

    await user.click(screen.getByLabelText("Select run r-2"));
    expect(onSelectionChange).toHaveBeenCalledWith(["r-2"]);
  });

  it("removes a run that was already selected", async () => {
    const user = userEvent.setup();
    const onSelectionChange = vi.fn();
    renderTable({ onSelectionChange, selectedIds: ["r-2", "r-3"] });

    await user.click(screen.getByLabelText("Select run r-2"));
    expect(onSelectionChange).toHaveBeenCalledWith(["r-3"]);
  });

  it("selects every run on the page at once, and clears them again", async () => {
    const user = userEvent.setup();
    const onSelectionChange = vi.fn();
    const { rerender } = renderTable({ onSelectionChange, selectedIds: [] });

    await user.click(screen.getByLabelText("Select every run on this page"));
    expect(onSelectionChange).toHaveBeenCalledWith(["r-3", "r-2", "r-1"]);

    rerender(
      <MemoryRouter>
        <RunTable
          runs={RUNS}
          onSelectionChange={onSelectionChange}
          selectedIds={["r-1", "r-2", "r-3"]}
        />
      </MemoryRouter>
    );
    await user.click(screen.getByLabelText("Select every run on this page"));
    expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  });
});

describe("RunTable rows", () => {
  it("offers Delete from a row menu only when the caller takes it", async () => {
    const user = userEvent.setup();
    const onDeleteRun = vi.fn();
    renderTable({ onDeleteRun });

    await user.click(screen.getByRole("button", { name: "Actions for run r-2" }));
    const menu = document.querySelector(".row-menu") as HTMLElement;
    await user.click(within(menu).getByRole("button", { name: "Delete" }));

    expect(onDeleteRun).toHaveBeenCalledWith(expect.objectContaining({ run_id: "r-2" }));
  });

  it("has no row menu when the caller offers no delete", () => {
    renderTable();
    expect(screen.queryByRole("button", { name: /Actions for run/ })).not.toBeInTheDocument();
  });

  it("expands one run onto its detail, and collapses it again", async () => {
    const user = userEvent.setup();
    renderTable({ renderExpanded: (entry) => <p>{`detail for ${entry.run_id}`}</p> });

    const expander = screen.getByRole("button", { name: "Details for run r-3" });
    expect(expander).toHaveAttribute("aria-expanded", "false");

    await user.click(expander);
    expect(screen.getByText("detail for r-3")).toBeInTheDocument();

    await user.click(expander);
    expect(screen.queryByText("detail for r-3")).not.toBeInTheDocument();
  });

  it("links the unit rather than the run when that column is shown", () => {
    renderTable({ columns: ["unit_serial", "run_id"] });
    expect(screen.getAllByRole("link", { name: "HC-001" })[0]).toHaveAttribute(
      "href",
      "/units/HC-001"
    );
  });
});

/** The kit's pager labels its buttons with icons, so it is reached by position. */
function nextPage(): HTMLElement {
  const bar = screen.getByText(/Page \d+ of \d+/).parentElement as HTMLElement;
  return within(bar).getAllByRole("button")[2];
}

describe("RunTable pagination", () => {
  const many = Array.from({ length: 25 }, (_index, at) =>
    run({ run_id: `r-${String(at).padStart(2, "0")}`, started_at: `2026-01-01T00:${at}:00Z` })
  );

  it("pages long lists and moves between the pages", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <RunTable runs={many} pageSize={10} />
      </MemoryRouter>
    );
    expect(screen.getAllByRole("row")).toHaveLength(11);
    expect(screen.getByText(/Page 1 of 3/)).toBeInTheDocument();

    await user.click(nextPage());

    expect(screen.getByText(/Page 2 of 3/)).toBeInTheDocument();
    expect(screen.getByText("r-14")).toBeInTheDocument();
  });

  it("renders every row when the caller turns paging off", () => {
    render(
      <MemoryRouter>
        <RunTable runs={many} pageSize={0} />
      </MemoryRouter>
    );
    expect(screen.getAllByRole("row")).toHaveLength(26);
  });

  it("steps back when filtering leaves fewer pages than the one being shown", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <RunTable runs={many} pageSize={10} />
      </MemoryRouter>
    );
    await user.click(nextPage());
    expect(screen.getByText(/Page 2 of 3/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Filter runs"), "r-01");

    await waitFor(() => expect(screen.getByText(/Page 1 of 1/)).toBeInTheDocument());
    expect(screen.getByText("r-01")).toBeInTheDocument();
  });
});

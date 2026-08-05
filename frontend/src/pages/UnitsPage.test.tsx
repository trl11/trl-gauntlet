import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Unit } from "@api/types";

import UnitsPage from "./UnitsPage";
import { pending } from "../test/queries";

const deleteUnit = vi.fn();
const listUnits = vi.fn();
const renameUnit = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    deleteUnit: (...args: unknown[]) => deleteUnit(...args),
    getUnit: () => Promise.resolve({ ...unit("HC-001", 4, 3, 1), notes: [] }),
    getUnitHistory: () => Promise.resolve({ runs: [] }),
    listUnitNotes: () => Promise.resolve({ notes: [] }),
    listUnits: () => listUnits(),
    renameUnit: (...args: unknown[]) => renameUnit(...args),
  };
});

function unit(serial: string, runs: number, passed: number, failed: number): Unit {
  return {
    failed,
    first_seen: "2026-01-01T00:00:00Z",
    last_run: {
      ended_at: "2026-01-03T00:00:00Z",
      run_id: "r-9",
      status: "passed",
      suite: "burn_in",
    },
    last_seen: "2026-01-03T00:00:00Z",
    note_count: 0,
    passed,
    run_count: runs,
    serial,
  };
}

function renderUnits(path = "/units") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/units" element={<UnitsPage />} />
          <Route path="/units/:serial" element={<UnitsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  listUnits.mockResolvedValue({
    units: [unit("HC-001", 4, 3, 1), unit("HC-002", 2, 0, 2)],
  });
  renameUnit.mockResolvedValue(unit("HC-003", 4, 3, 1));
  deleteUnit.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("UnitsPage", () => {
  it("lists every unit with its counters and pass rate", async () => {
    renderUnits();
    expect(await screen.findByText("HC-001")).toBeInTheDocument();
    expect(screen.getByText("HC-002")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("0%")).toBeInTheDocument();
  });

  it("filters by serial", async () => {
    renderUnits();
    await screen.findByText("HC-001");
    const filterButton = document.querySelector(".fa-filter")!.closest("button")!;
    await userEvent.click(filterButton);
    await userEvent.selectOptions(screen.getByRole("combobox"), "HC-002");
    const table = screen.getByRole("table");
    expect(within(table).queryByText("HC-001")).toBeNull();
    expect(within(table).getByText("HC-002")).toBeInTheDocument();
  });

  it("sorts by a column when its header is pressed", async () => {
    renderUnits();
    await screen.findByText("HC-001");
    await userEvent.click(screen.getByRole("button", { name: /serial/i }));
    const header = screen.getByRole("columnheader", { name: /serial/i });
    expect(header).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getAllByRole("button", { name: "HC-001" })[0]).toBeInTheDocument();
  });

  it("shows a table skeleton while the units are being read", () => {
    listUnits.mockReturnValue(pending());
    const { container } = renderUnits();
    expect(container.querySelectorAll("tbody tr")).toHaveLength(6);
    expect(screen.queryByText("No units yet")).not.toBeInTheDocument();
  });

  it("reports units that could not be read", async () => {
    listUnits.mockRejectedValue(new Error("units index locked"));
    renderUnits();
    expect(await screen.findByText("Could not read the units")).toBeInTheDocument();
    expect(screen.getByText("units index locked")).toBeInTheDocument();
  });

  it("shows an empty state when nothing has been on the bench", async () => {
    listUnits.mockResolvedValue({ units: [] });
    renderUnits();
    expect(await screen.findByText("No units yet")).toBeInTheDocument();
  });

  it("deletes a unit with its runs, counting them in the confirmation", async () => {
    renderUnits();
    await screen.findByText("HC-001");
    await userEvent.click(screen.getByRole("button", { name: "Actions for unit HC-001" }));
    const menu = document.querySelector(".row-menu") as HTMLElement;
    await userEvent.click(within(menu).getByRole("button", { name: "Delete" }));
    expect(screen.getByText(/Delete unit HC-001 and 4 runs\?/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(deleteUnit).toHaveBeenCalledWith("HC-001");
  });

  it("batch-deletes every selected unit with its runs", async () => {
    renderUnits();
    await screen.findByText("HC-001");
    await userEvent.click(screen.getByLabelText("Select unit HC-001"));
    await userEvent.click(screen.getByLabelText("Select unit HC-002"));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByText(/Delete 2 units and 6 runs\?/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(deleteUnit).toHaveBeenCalledWith("HC-001");
    expect(deleteUnit).toHaveBeenCalledWith("HC-002");
  });

  it("shows one unit when the route names it", async () => {
    renderUnits("/units/HC-001");
    const heading = await screen.findByRole("heading", { name: "HC-001" });
    expect(within(heading).getByText("HC-001")).toBeInTheDocument();
  });

  it("rejects a serial the API would not accept", async () => {
    renderUnits("/units/HC-001");
    await screen.findByRole("heading", { name: "HC-001" });
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));

    const field = screen.getByLabelText("New serial");
    await userEvent.clear(field);
    await userEvent.type(field, "no spaces here");
    expect(screen.getByText("That is not a valid serial.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Rename" }).at(-1)).toBeDisabled();
  });

  it("renames a unit", async () => {
    renderUnits("/units/HC-001");
    await screen.findByRole("heading", { name: "HC-001" });
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));

    const field = screen.getByLabelText("New serial");
    await userEvent.clear(field);
    await userEvent.type(field, "HC-003");
    await userEvent.click(screen.getAllByRole("button", { name: "Rename" }).at(-1)!);
    expect(renameUnit).toHaveBeenCalledWith("HC-001", "HC-003");
  });

  it("surfaces a collision reported by the server", async () => {
    renameUnit.mockRejectedValue(new Error("unit 'HC-002' already exists"));
    renderUnits("/units/HC-001");
    await screen.findByRole("heading", { name: "HC-001" });
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));

    const field = screen.getByLabelText("New serial");
    await userEvent.clear(field);
    await userEvent.type(field, "HC-002");
    await userEvent.click(screen.getAllByRole("button", { name: "Rename" }).at(-1)!);
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });
});

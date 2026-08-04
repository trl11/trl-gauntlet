import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import SeriesPicker from "./SeriesPicker";

const SMALL = ["rail.volts", "temp_c"];
const LARGE = [
  ...Array.from({ length: 10 }, (_, i) => `cpu.per_core.cpu${i}`),
  ...Array.from({ length: 5 }, (_, i) => `memory.field${i}`),
];

/** The picker sits behind a Popover trigger, closed by default. */
function trigger() {
  return screen.getByRole("button", { name: /measurements/i });
}

async function open() {
  await userEvent.click(trigger());
}

describe("SeriesPicker", () => {
  it("starts closed, with the trigger reporting how many are selected", () => {
    render(<SeriesPicker names={LARGE} selected={["cpu.per_core.cpu0"]} onChange={vi.fn()} />);
    expect(trigger()).toHaveTextContent("Measurements (1/15)");
    expect(screen.queryByPlaceholderText("Measurements")).not.toBeInTheDocument();
  });

  it("opens on trigger click and closes on an outside click", async () => {
    render(<SeriesPicker names={SMALL} selected={[]} onChange={vi.fn()} />);
    await open();
    expect(screen.getByLabelText("rail.volts")).toBeInTheDocument();

    await userEvent.click(document.body);
    expect(screen.queryByLabelText("rail.volts")).not.toBeInTheDocument();
  });

  it("stays flat with full names below the grouping threshold", async () => {
    render(<SeriesPicker names={SMALL} selected={[]} onChange={vi.fn()} />);
    await open();
    expect(screen.getByLabelText("rail.volts")).toBeInTheDocument();
    expect(screen.getByLabelText("temp_c")).toBeInTheDocument();
    expect(screen.queryByText("cpu")).not.toBeInTheDocument();
  });

  it("groups a large set into one section per prefix", async () => {
    render(<SeriesPicker names={LARGE} selected={[]} onChange={vi.fn()} />);
    await open();
    expect(screen.getByText("cpu")).toBeInTheDocument();
    expect(screen.getByText("memory")).toBeInTheDocument();
  });

  it("picking a name inside a group reports it with its full name", async () => {
    const onChange = vi.fn();
    render(<SeriesPicker names={LARGE} selected={[]} onChange={onChange} />);
    await open();
    await userEvent.click(screen.getByText("cpu"));
    await userEvent.click(screen.getByLabelText("per_core.cpu2"));
    expect(onChange).toHaveBeenCalledWith(["cpu.per_core.cpu2"]);
  });

  it("sorts names inside a group numerically, not lexically", async () => {
    const onChange = vi.fn();
    render(<SeriesPicker names={LARGE} selected={[]} onChange={onChange} />);
    await open();
    await userEvent.click(screen.getByText("cpu"));
    const labels = screen.getAllByText(/per_core\.cpu\d+/).map((el) => el.textContent);
    expect(labels).toEqual([
      "per_core.cpu0",
      "per_core.cpu1",
      "per_core.cpu2",
      "per_core.cpu3",
      "per_core.cpu4",
      "per_core.cpu5",
      "per_core.cpu6",
      "per_core.cpu7",
      "per_core.cpu8",
      "per_core.cpu9",
    ]);
  });

  it("filtering flattens matches across groups", async () => {
    render(<SeriesPicker names={LARGE} selected={[]} onChange={vi.fn()} />);
    await open();
    await userEvent.type(screen.getByPlaceholderText("Measurements"), "field2");
    expect(screen.getByLabelText("memory.field2")).toBeInTheDocument();
    expect(screen.queryByLabelText("cpu.per_core.cpu0")).not.toBeInTheDocument();
  });

  it("hides Clear all until something is selected, then clears everything", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<SeriesPicker names={LARGE} selected={[]} onChange={onChange} />);
    await open();
    expect(screen.queryByText("Clear all")).not.toBeInTheDocument();

    rerender(<SeriesPicker names={LARGE} selected={["cpu.per_core.cpu0"]} onChange={onChange} />);
    await userEvent.click(screen.getByText("Clear all"));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("folds window_s into an Other group", async () => {
    render(<SeriesPicker names={[...LARGE, "window_s"]} selected={[]} onChange={vi.fn()} />);
    await open();
    expect(screen.getByText("other")).toBeInTheDocument();
  });
});

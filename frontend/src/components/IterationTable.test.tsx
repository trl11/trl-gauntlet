import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import IterationTable, { type IterationRow } from "./IterationTable";
import type { MetricSample } from "./MetricsChart";

const ITERATIONS: IterationRow[] = [
  { elapsed_run_s: 2, images: [], iteration: 1, reason: "", success: true },
  { elapsed_run_s: 5, images: [], iteration: 2, reason: "rail low", success: false },
];

const SAMPLES: MetricSample[] = [
  { elapsed_s: 2, iteration: 1, seq: 1, ts: 1, values: { volts: 3.3 } },
  { elapsed_s: 5, iteration: 2, seq: 2, ts: 2, values: { volts: 2.9 } },
];

describe("IterationTable", () => {
  it("says so before any iteration completes", () => {
    render(<IterationTable iterations={[]} samples={[]} />);
    expect(screen.getByText(/no iterations/i)).toBeInTheDocument();
  });

  it("renders one row per iteration with its outcome", () => {
    render(<IterationTable iterations={ITERATIONS} samples={SAMPLES} />);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("FAIL")).toBeInTheDocument();
    expect(screen.getByText("rail low")).toBeInTheDocument();
  });

  it("takes each iteration's duration from the gap to the one before it", () => {
    render(<IterationTable iterations={ITERATIONS} samples={SAMPLES} />);
    expect(screen.getByText("2s")).toBeInTheDocument();
    expect(screen.getByText("3s")).toBeInTheDocument();
  });

  it("shows the sample values recorded against an iteration, aligned under their own column", () => {
    render(<IterationTable iterations={ITERATIONS} samples={SAMPLES} />);
    expect(screen.getByRole("columnheader", { name: "volts" })).toBeInTheDocument();
    expect(screen.getByText("3.3")).toBeInTheDocument();
    expect(screen.getByText("2.9")).toBeInTheDocument();
  });

  it("filters down to the failures", async () => {
    render(<IterationTable iterations={ITERATIONS} samples={SAMPLES} />);
    await userEvent.click(screen.getByLabelText("Failures only"));
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.queryByText("PASS")).not.toBeInTheDocument();
  });

  it("marks and scrolls to the iteration it was opened at", () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const { container } = render(
      <IterationTable iterations={ITERATIONS} samples={SAMPLES} selected={2} />
    );
    expect(container.querySelectorAll("tr.is-selected")).toHaveLength(1);
    expect(container.querySelector("tr.is-selected")).toHaveTextContent("rail low");
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("says so when a filter to failures leaves nothing", async () => {
    render(<IterationTable iterations={[ITERATIONS[0]]} samples={SAMPLES} />);
    await userEvent.click(screen.getByLabelText("Failures only"));
    expect(screen.getByText("Every iteration passed.")).toBeInTheDocument();
  });
});

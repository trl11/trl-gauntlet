import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import IterationMap, { type PhaseRow } from "./IterationMap";
import type { IterationRow } from "./IterationTable";

const ITERATIONS: IterationRow[] = [
  { elapsed_run_s: 2, images: [], iteration: 1, reason: "", success: true },
  { elapsed_run_s: 5, images: [], iteration: 2, reason: "rail low", success: false },
];

const PHASES: PhaseRow[] = [
  { detail: {}, elapsed_s: 1.5, iteration: 1, phase: "soak", success: true },
  { detail: {}, elapsed_s: 0.5, iteration: 1, phase: "check", success: true },
  { detail: {}, elapsed_s: 3, iteration: 2, phase: "soak", success: false },
];

describe("IterationMap", () => {
  it("says so before anything is reported", () => {
    render(<IterationMap iterations={[]} phases={[]} />);
    expect(screen.getByText(/no iterations/i)).toBeInTheDocument();
  });

  it("draws one square per iteration, however many phases each holds", () => {
    render(<IterationMap iterations={ITERATIONS} phases={PHASES} />);
    expect(screen.getAllByRole("button")).toHaveLength(2);
    expect(screen.getByText("2 iterations, 1 failed")).toBeInTheDocument();
  });

  it("names each square by its outcome, its own duration and its reason", () => {
    render(<IterationMap iterations={ITERATIONS} phases={PHASES} />);
    expect(screen.getByRole("button", { name: "#1 · passed · 2s" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "#2 · failed · 3s · rail low" })).toBeInTheDocument();
  });

  it("summarises the phases of an iteration on hover", async () => {
    render(<IterationMap iterations={ITERATIONS} phases={PHASES} />);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    await userEvent.hover(screen.getByRole("button", { name: "#1 · passed · 2s" }));
    expect(await screen.findByRole("tooltip")).toHaveTextContent("soak 1s · check 500ms");
    await userEvent.unhover(screen.getByRole("button", { name: "#1 · passed · 2s" }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("reports the iteration a click opens", async () => {
    const onSelect = vi.fn();
    render(<IterationMap iterations={ITERATIONS} phases={PHASES} onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("button", { name: "#2 · failed · 3s · rail low" }));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("keeps phases no iteration reported rather than dropping them", () => {
    const phases: PhaseRow[] = [
      { detail: {}, elapsed_s: 4, iteration: null, phase: "setup", success: true },
    ];
    render(<IterationMap iterations={ITERATIONS} phases={phases} />);
    expect(screen.getByRole("button", { name: "run · passed · 4s" })).toBeInTheDocument();
  });

  it("marks an iteration still in flight from the phases it has reported", () => {
    const phases: PhaseRow[] = [
      { detail: {}, elapsed_s: 1, iteration: 3, phase: "soak", success: false },
    ];
    render(<IterationMap iterations={ITERATIONS} phases={phases} />);
    expect(screen.getByRole("button", { name: "#3 · failed · 1s" })).toBeInTheDocument();
  });
});

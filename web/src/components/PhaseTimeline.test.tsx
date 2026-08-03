import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PhaseTimeline, { type PhaseRow } from "./PhaseTimeline";

const PHASES: PhaseRow[] = [
  { detail: {}, elapsed_s: 2, iteration: 1, phase: "soak", success: true },
  { detail: { setpoint: 40 }, elapsed_s: 6, iteration: 1, phase: "measure", success: false },
  { detail: {}, elapsed_s: 1, iteration: 2, phase: "soak", success: true },
];

describe("PhaseTimeline", () => {
  it("says so when no phases were reported", () => {
    render(<PhaseTimeline phases={[]} />);
    expect(screen.getByText(/no phases/i)).toBeInTheDocument();
  });

  it("groups phases into one track per iteration", () => {
    const { container } = render(<PhaseTimeline phases={PHASES} />);
    expect(container.querySelectorAll(".phase-timeline__track")).toHaveLength(2);
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
  });

  it("shows each track's total duration", () => {
    render(<PhaseTimeline phases={PHASES} />);
    expect(screen.getByText("8s")).toBeInTheDocument();
    expect(screen.getByText("1s")).toBeInTheDocument();
  });

  it("marks a failed phase", () => {
    const { container } = render(<PhaseTimeline phases={PHASES} />);
    expect(container.querySelectorAll(".phase-timeline__segment--failed")).toHaveLength(1);
  });

  it("describes a phase for assistive technology", () => {
    render(<PhaseTimeline phases={PHASES} />);
    expect(screen.getByLabelText("measure - 6s - failed - setpoint=40")).toBeInTheDocument();
  });

  it("labels a phase with no iteration as belonging to the run", () => {
    render(
      <PhaseTimeline
        phases={[{ detail: {}, elapsed_s: 1, iteration: null, phase: "setup", success: true }]}
      />
    );
    expect(screen.getByText("run")).toBeInTheDocument();
  });
});

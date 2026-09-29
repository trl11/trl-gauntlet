import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Verdict } from "@api/types";
import VerdictSummary from "./VerdictSummary";

const VERDICT: Partial<Verdict> = {
  duration_s: 65,
  failures: 1,
  passed: false,
  reason: "rail voltage out of tolerance on cycle 7",
  results: [
    {
      format: "percent",
      highlight: true,
      key: "yield",
      label: "Yield",
      precision: null,
      unit: "",
      value: 87.5,
    },
    {
      format: "bytes",
      highlight: false,
      key: "captured",
      label: "Captured",
      precision: null,
      unit: "",
      value: 2048,
    },
  ],
  successes: 6,
  total_iterations: 7,
};

describe("VerdictSummary", () => {
  it("says so when no verdict was written", () => {
    render(<VerdictSummary verdict={null} />);
    expect(screen.getByText(/no verdict/i)).toBeInTheDocument();
  });

  it("shows the counters", () => {
    render(<VerdictSummary verdict={VERDICT} />);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
  });

  it("formats each headline figure the way the suite asked", () => {
    render(<VerdictSummary verdict={VERDICT} />);
    expect(screen.getByText("87.5%")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
  });

  it("renders the suite's own summary text as markdown, not raw source", () => {
    render(<VerdictSummary verdict={VERDICT} summaryText="# Cycle report" />);
    expect(screen.getByRole("heading", { name: "Cycle report" })).toBeInTheDocument();
    expect(screen.queryByText("# Cycle report")).not.toBeInTheDocument();
  });

  it("formats integers, durations, decimals and anything else the suite reports", () => {
    const row = { highlight: false, label: "", precision: null, unit: "" };
    render(
      <VerdictSummary
        verdict={{
          results: [
            { ...row, format: "int", key: "frames", value: 12345.6 },
            { ...row, format: "duration", key: "soak", value: 90 },
            { ...row, format: "decimal", key: "ripple", precision: 3, unit: "mV", value: 0.12345 },
            { ...row, format: "text", key: "count", value: 42 },
            { ...row, format: "text", key: "firmware", value: "v2.1" },
            { ...row, format: "int", key: "missing", value: null },
          ],
        }}
      />
    );

    expect(screen.getByText("12,346")).toBeInTheDocument();
    expect(screen.getByText("1m 30s")).toBeInTheDocument();
    expect(screen.getByText("0.123")).toBeInTheDocument();
    expect(screen.getByText("mV")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("v2.1")).toBeInTheDocument();
    expect(screen.getByText("-")).toBeInTheDocument();
    // A figure with no label of its own is named by its key.
    expect(screen.getByText("firmware")).toBeInTheDocument();
  });

  it("counts nothing for a partial verdict with no counters yet", () => {
    render(<VerdictSummary verdict={{}} />);
    expect(screen.getAllByText("0")).toHaveLength(3);
    expect(screen.queryByText("Results")).not.toBeInTheDocument();
  });

  it("says when a run stopped early or was aborted, and why", () => {
    const { rerender } = render(
      <VerdictSummary
        verdict={{ abort_reason: "operator abort", aborted: true, stopped_early: true }}
      />
    );
    expect(screen.getByText("Stopped early")).toBeInTheDocument();
    expect(screen.getByText("operator abort")).toBeInTheDocument();

    rerender(<VerdictSummary verdict={{ abort_reason: "", aborted: true }} />);
    expect(screen.getByText("Aborted")).toBeInTheDocument();
    expect(screen.getByText("yes")).toBeInTheDocument();
  });
});

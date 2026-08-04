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

  it("shows the counters and the duration", () => {
    render(<VerdictSummary verdict={VERDICT} />);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("1m 5s")).toBeInTheDocument();
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
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import OutcomeChart from "./OutcomeChart";

describe("OutcomeChart", () => {
  it("summarises every bucket in text", () => {
    render(
      <OutcomeChart
        buckets={[
          { label: "Last 24h", passed: 3, failed: 1, other: 0 },
          { label: "Last 7d", passed: 9, failed: 2, other: 1 },
        ]}
      />
    );
    expect(screen.getByText(/Last 24h: 3 passed, 1 failed, 0 other/)).toBeInTheDocument();
    expect(screen.getByText(/Last 7d: 9 passed, 2 failed, 1 other/)).toBeInTheDocument();
  });

  it("shows an empty state when nothing has finished", () => {
    render(
      <OutcomeChart
        buckets={[
          { label: "Last 24h", passed: 0, failed: 0, other: 0 },
          { label: "Last 7d", passed: 0, failed: 0, other: 0 },
        ]}
      />
    );
    expect(screen.getByText("No outcomes yet")).toBeInTheDocument();
  });

  it("shows an empty state with no buckets at all", () => {
    render(<OutcomeChart buckets={[]} />);
    expect(screen.getByText("No outcomes yet")).toBeInTheDocument();
  });
});

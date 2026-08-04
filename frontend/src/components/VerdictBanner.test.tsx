import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Verdict } from "@api/types";
import VerdictBanner from "./VerdictBanner";

describe("VerdictBanner", () => {
  it("renders nothing when no verdict was written", () => {
    const { container } = render(<VerdictBanner verdict={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the outcome and its reason", () => {
    const verdict: Partial<Verdict> = {
      passed: false,
      reason: "rail voltage out of tolerance on cycle 7",
    };
    render(<VerdictBanner verdict={verdict} />);
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    expect(screen.getByText("rail voltage out of tolerance on cycle 7")).toBeInTheDocument();
  });

  it("marks a passing run as passed", () => {
    render(<VerdictBanner verdict={{ passed: true, reason: "" }} />);
    expect(screen.getByText("PASSED")).toBeInTheDocument();
  });
});

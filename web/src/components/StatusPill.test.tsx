import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import StatusPill from "./StatusPill";

describe("StatusPill", () => {
  it.each([
    ["passed", "PASSED"],
    ["failed", "FAILED"],
    ["aborted", "ABORTED"],
    ["error", "ERROR"],
    ["running", "RUNNING"],
    ["interrupted", "INTERRUPTED"],
  ])("renders %s in upper case", (status, label) => {
    render(<StatusPill status={status} />);
    expect(screen.getByRole("status")).toHaveTextContent(label);
  });

  it("prefers the verdict over the status", () => {
    render(<StatusPill status="failed" verdict="PASS" />);
    expect(screen.getByRole("status")).toHaveTextContent("PASS");
  });

  it("labels the pill for assistive technology", () => {
    render(<StatusPill status="passed" />);
    expect(screen.getByRole("status")).toHaveAccessibleName("Status: passed");
  });

  it("falls back to UNKNOWN with nothing to show", () => {
    render(<StatusPill status={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("UNKNOWN");
    expect(screen.getByRole("status")).toHaveAccessibleName("Status: unknown");
  });

  it("marks an in-flight run as live", () => {
    const { container } = render(<StatusPill status="running" />);
    expect(container.querySelector(".status-pill--live")).not.toBeNull();
  });

  it("does not mark a finished run as live", () => {
    const { container } = render(<StatusPill status="passed" />);
    expect(container.querySelector(".status-pill--live")).toBeNull();
  });
});

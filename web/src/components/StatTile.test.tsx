import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import StatTile from "./StatTile";

describe("StatTile", () => {
  it("renders the label, value and detail", () => {
    render(<StatTile label="CPU" value="42.0%" detail="8 cores" />);
    expect(screen.getByText("CPU")).toBeInTheDocument();
    expect(screen.getByText("42.0%")).toBeInTheDocument();
    expect(screen.getByText("8 cores")).toBeInTheDocument();
  });

  it("exposes the meter to assistive technology", () => {
    render(<StatTile label="Memory" value="61%" percent={61.4} />);
    const meter = screen.getByRole("progressbar", { name: "Memory usage" });
    expect(meter).toHaveAttribute("aria-valuenow", "61");
  });

  it("clamps a percentage outside 0-100", () => {
    render(<StatTile label="Disk" value="120%" percent={120} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("omits the meter when there is no percentage", () => {
    render(<StatTile label="Load average" value="0.20" />);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("draws a sparkline once there are two samples", () => {
    const { container } = render(<StatTile label="CPU" value="10%" samples={[1, 5, 3]} />);
    const line = container.querySelector(".stat-tile__spark polyline");
    expect(line).toHaveAttribute("points", "0.0,23.0 50.0,1.0 100.0,12.0");
  });

  it("draws no sparkline for a single sample", () => {
    const { container } = render(<StatTile label="CPU" value="10%" samples={[1]} />);
    expect(container.querySelector(".stat-tile__spark")).toBeNull();
  });

  it("marks the tone on the tile", () => {
    const { container } = render(<StatTile label="CPU" value="99%" tone="critical" />);
    expect(container.querySelector(".stat-tile--critical")).not.toBeNull();
  });
});

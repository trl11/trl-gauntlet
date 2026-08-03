import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ReadoutChart, { type ReadoutSeries } from "./ReadoutChart";

const SERIES: ReadoutSeries[] = [{ key: "channels.1.voltage", label: "Voltage" }];

function history(...values: number[]): Array<Record<string, number>> {
  return values.map((value) => ({ "channels.1.voltage": value }));
}

describe("ReadoutChart", () => {
  it("draws nothing without a series", () => {
    const { container } = render(<ReadoutChart history={history(1, 2)} series={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("draws nothing until there are two samples", () => {
    const { container } = render(<ReadoutChart history={history(1)} series={SERIES} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names every series in its legend", () => {
    render(
      <ReadoutChart
        history={[
          { a: 1, b: 4 },
          { a: 2, b: 5 },
        ]}
        series={[
          { key: "a", label: "Voltage" },
          { key: "b", label: "Current" },
        ]}
      />
    );
    expect(screen.getByText("Voltage")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
  });
});

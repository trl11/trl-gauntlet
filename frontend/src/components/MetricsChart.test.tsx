import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import MetricsChart, { type MetricSample } from "./MetricsChart";

const SAMPLES: MetricSample[] = [
  { elapsed_s: 0, iteration: 1, seq: 1, ts: 100, values: { "rail.volts": 3.3, temp_c: 40 } },
  { elapsed_s: 2, iteration: 2, seq: 2, ts: 102, values: { "rail.volts": 3.2, temp_c: 41 } },
];

describe("MetricsChart", () => {
  it("says so when the run reported no numbers", () => {
    render(<MetricsChart samples={[]} />);
    expect(screen.getByText(/no numeric metrics/i)).toBeInTheDocument();
  });

  it("discovers the series from the data", () => {
    render(<MetricsChart samples={SAMPLES} />);
    expect(screen.getByLabelText("rail.volts")).toBeChecked();
    expect(screen.getByLabelText("temp_c")).toBeChecked();
    expect(screen.getByRole("heading", { name: "rail.volts" })).toBeInTheDocument();
  });

  it("charts nothing once every series is switched off", async () => {
    render(<MetricsChart samples={SAMPLES} />);
    await userEvent.click(screen.getByLabelText("rail.volts"));
    await userEvent.click(screen.getByLabelText("temp_c"));
    expect(screen.getByText("Pick a series to chart it.")).toBeInTheDocument();
  });

  it("charts only the series left switched on", async () => {
    render(<MetricsChart samples={SAMPLES} />);
    await userEvent.click(screen.getByLabelText("temp_c"));
    expect(screen.getByRole("heading", { name: "rail.volts" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "temp_c" })).not.toBeInTheDocument();
  });

  it("charts at most four series before the operator chooses", () => {
    const wide: MetricSample[] = [
      { elapsed_s: 0, iteration: 1, seq: 1, ts: 1, values: { a: 1, b: 2, c: 3, d: 4, e: 5 } },
    ];
    render(<MetricsChart samples={wide} />);
    expect(screen.getByLabelText("e")).not.toBeChecked();
    expect(screen.queryByRole("heading", { name: "e" })).not.toBeInTheDocument();
  });
});

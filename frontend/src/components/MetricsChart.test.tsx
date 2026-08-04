import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import MetricsChart, { type MetricSample } from "./MetricsChart";

const SAMPLES: MetricSample[] = [
  { elapsed_s: 0, iteration: 1, seq: 1, ts: 100, values: { "rail.volts": 3.3, temp_c: 40 } },
  { elapsed_s: 2, iteration: 2, seq: 2, ts: 102, values: { "rail.volts": 3.2, temp_c: 41 } },
];

beforeEach(() => {
  localStorage.clear();
});

describe("MetricsChart", () => {
  it("says so when the run reported no numbers", () => {
    render(<MetricsChart runId="run-1" samples={[]} defaultMetrics={[]} />);
    expect(screen.getByText(/no numeric metrics/i)).toBeInTheDocument();
  });

  it("discovers the series from the data", async () => {
    render(<MetricsChart runId="run-1" samples={SAMPLES} defaultMetrics={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /measurements/i }));
    expect(screen.getByLabelText("rail.volts")).toBeChecked();
    expect(screen.getByLabelText("temp_c")).toBeChecked();
    expect(screen.getByRole("heading", { name: "rail.volts" })).toBeInTheDocument();
  });

  it("charts nothing once every series is switched off", async () => {
    render(<MetricsChart runId="run-1" samples={SAMPLES} defaultMetrics={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /measurements/i }));
    await userEvent.click(screen.getByLabelText("rail.volts"));
    await userEvent.click(screen.getByLabelText("temp_c"));
    expect(screen.getByText("Pick a series to chart it.")).toBeInTheDocument();
  });

  it("charts only the series left switched on", async () => {
    render(<MetricsChart runId="run-1" samples={SAMPLES} defaultMetrics={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /measurements/i }));
    await userEvent.click(screen.getByLabelText("temp_c"));
    expect(screen.getByRole("heading", { name: "rail.volts" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "temp_c" })).not.toBeInTheDocument();
  });

  it("charts at most four series before the operator chooses, when the suite declares no defaults", async () => {
    const wide: MetricSample[] = [
      { elapsed_s: 0, iteration: 1, seq: 1, ts: 1, values: { a: 1, b: 2, c: 3, d: 4, e: 5 } },
    ];
    render(<MetricsChart runId="run-1" samples={wide} defaultMetrics={[]} />);
    await userEvent.click(screen.getByRole("button", { name: /measurements/i }));
    expect(screen.getByLabelText("e")).not.toBeChecked();
    expect(screen.queryByRole("heading", { name: "e" })).not.toBeInTheDocument();
  });

  it("charts the suite's declared defaults instead of the first few, when it has any", () => {
    const wide: MetricSample[] = [
      { elapsed_s: 0, iteration: 1, seq: 1, ts: 1, values: { a: 1, b: 2, c: 3, d: 4, e: 5 } },
    ];
    render(<MetricsChart runId="run-1" samples={wide} defaultMetrics={["e", "c"]} />);
    expect(screen.getByRole("heading", { name: "e" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "c" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "a" })).not.toBeInTheDocument();
  });

  it("ignores declared defaults the run never actually reported", () => {
    render(
      <MetricsChart runId="run-1" samples={SAMPLES} defaultMetrics={["temp_c", "not_reported"]} />
    );
    expect(screen.getByRole("heading", { name: "temp_c" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "not_reported" })).not.toBeInTheDocument();
  });

  it("remembers the operator's pick for this run across a remount", async () => {
    const { unmount } = render(
      <MetricsChart runId="run-1" samples={SAMPLES} defaultMetrics={[]} />
    );
    await userEvent.click(screen.getByRole("button", { name: /measurements/i }));
    await userEvent.click(screen.getByLabelText("temp_c"));
    unmount();

    render(<MetricsChart runId="run-1" samples={SAMPLES} defaultMetrics={[]} />);
    expect(screen.getByRole("heading", { name: "rail.volts" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "temp_c" })).not.toBeInTheDocument();
  });

  it("removes a series from its panel's own remove button, without opening the picker", async () => {
    render(<MetricsChart runId="run-1" samples={SAMPLES} defaultMetrics={[]} />);
    expect(screen.getByRole("heading", { name: "rail.volts" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Remove rail.volts from chart" }));
    expect(screen.queryByRole("heading", { name: "rail.volts" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "temp_c" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Measurements")).not.toBeInTheDocument();
  });

  it("does not carry one run's pick over to another", async () => {
    const { unmount } = render(
      <MetricsChart runId="run-1" samples={SAMPLES} defaultMetrics={[]} />
    );
    await userEvent.click(screen.getByRole("button", { name: /measurements/i }));
    await userEvent.click(screen.getByLabelText("temp_c"));
    unmount();

    render(<MetricsChart runId="run-2" samples={SAMPLES} defaultMetrics={[]} />);
    expect(screen.getByRole("heading", { name: "rail.volts" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "temp_c" })).toBeInTheDocument();
  });
});

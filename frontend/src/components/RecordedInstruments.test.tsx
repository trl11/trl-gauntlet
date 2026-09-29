import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@api/client";
import type { InstrumentRecord } from "@api/types";

import RecordedInstruments from "./RecordedInstruments";

const getRunInstruments = vi.fn();
const getRunInstrumentTrace = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getRunInstruments: (...args: unknown[]) => getRunInstruments(...args),
    getRunInstrumentTrace: (...args: unknown[]) => getRunInstrumentTrace(...args),
  };
});

const record: InstrumentRecord = {
  instruments: [
    {
      description: "a bench supply",
      kind: "psu",
      name: "psu",
      readings: [
        {
          count: 42,
          group: "",
          key: "voltage",
          label: "Voltage",
          last: 5.01,
          max: 5.02,
          mean: 5.0,
          min: 4.98,
          precision: 2,
          unit: "V",
        },
      ],
    },
    {
      description: "",
      kind: "logic",
      name: "logic",
      readings: [],
    },
  ],
  interval_s: 1,
  ticks: 42,
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RecordedInstruments runId="RUN-0001" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getRunInstruments.mockResolvedValue(record);
  getRunInstrumentTrace.mockResolvedValue([
    { at: "2026-01-01T00:00:00.000Z", instrument: "psu", t: 0, values: { voltage: 5.0 } },
    { at: "2026-01-01T00:00:01.000Z", instrument: "psu", t: 1, values: { voltage: 5.01 } },
  ]);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("RecordedInstruments", () => {
  it("shows each reading under the label its instrument gave it", async () => {
    renderPanel();
    const row = (await screen.findByText("Voltage")).closest("tr");
    expect(row).not.toBeNull();
    expect(row).toHaveTextContent("4.98");
    expect(row).toHaveTextContent("5.00");
    expect(row).toHaveTextContent("5.02");
    expect(row).toHaveTextContent("42");
  });

  it("shows a reading's key beneath the name its channel carried", async () => {
    renderPanel();
    expect(await screen.findByText("Voltage")).toBeInTheDocument();
    expect(screen.getByText("voltage")).toBeInTheDocument();
  });

  it("says how often the bench was read", async () => {
    renderPanel();
    expect(await screen.findByText(/Read every 1s, 42 times/)).toBeInTheDocument();
  });

  it("says so when an instrument published nothing", async () => {
    renderPanel();
    expect(await screen.findByText(/published no readings/)).toBeInTheDocument();
  });

  it("stands down when the run recorded nothing", async () => {
    getRunInstruments.mockRejectedValue(new ApiError(404, "no such artifact", "/api"));
    renderPanel();
    expect(await screen.findByText("Nothing recorded")).toBeInTheDocument();
  });

  it("expands a clicked reading's row into its chart and collapses it again", async () => {
    renderPanel();
    const button = await screen.findByRole("button", { name: /Voltage/ });
    expect(button).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    await waitFor(() =>
      expect(document.querySelector(".recorded-instruments__chart")).not.toBeNull()
    );

    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector(".recorded-instruments__chart-row")).toBeNull();
  });

  it("draws each reading's trend in its row from the trace", async () => {
    renderPanel();
    const row = (await screen.findByText("Voltage")).closest("tr")!;
    await waitFor(() => expect(row.querySelector(".sparkline__line")).not.toBeNull());
    expect(getRunInstrumentTrace).toHaveBeenCalledWith("RUN-0001");
  });

  it("thins a long trace to a bounded number of points", async () => {
    getRunInstrumentTrace.mockResolvedValue(
      Array.from({ length: 1000 }, (_, index) => ({
        at: "2026-01-01T00:00:00.000Z",
        instrument: "psu",
        t: index,
        values: { voltage: index },
      }))
    );
    renderPanel();
    const row = (await screen.findByText("Voltage")).closest("tr")!;
    await waitFor(() => expect(row.querySelector(".sparkline__line")).not.toBeNull());
    const points = row.querySelector(".sparkline__line")!.getAttribute("points")!.split(" ");
    expect(points).toHaveLength(120);
  });

  it("says so when the run kept no trace to chart from", async () => {
    getRunInstrumentTrace.mockRejectedValue(new ApiError(404, "no such artifact", "/api"));
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /Voltage/ }));
    expect(await screen.findByText(/kept no trace/)).toBeInTheDocument();
  });
});

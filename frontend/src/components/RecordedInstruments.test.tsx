import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@api/client";
import type { InstrumentRecord } from "@api/types";

import RecordedInstruments from "./RecordedInstruments";

const getRunInstruments = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getRunInstruments: (...args: unknown[]) => getRunInstruments(...args),
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

function renderPanel(onSelectReading: (key: string) => void = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RecordedInstruments runId="RUN-0001" onSelectReading={onSelectReading} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getRunInstruments.mockResolvedValue(record);
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

  it("sends the instrument-prefixed key of the reading clicked", async () => {
    const onSelectReading = vi.fn();
    renderPanel(onSelectReading);
    await userEvent.click(await screen.findByRole("button", { name: /Voltage/ }));
    expect(onSelectReading).toHaveBeenCalledWith("psu.voltage");
  });
});

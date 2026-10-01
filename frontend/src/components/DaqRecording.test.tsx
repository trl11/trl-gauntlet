import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DaqRecording as Recording, DaqWindow, UpsetEntry } from "@api/types";
import DaqRecording from "./DaqRecording";

const getDaqRecording = vi.fn();
const getDaqWindow = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getDaqRecording: (...args: unknown[]) => getDaqRecording(...args),
    getDaqWindow: (...args: unknown[]) => getDaqWindow(...args),
  };
});

const channels = [{ key: "1", label: "CH 1", unit: "V" }];

const one: Recording = {
  instruments: [
    { channels, end_s: 100, instrument: "daq.0", rate_hz: 2000, rows: 200_000, start_s: 0 },
  ],
  origin: 1_700_000_000,
};

const two: Recording = {
  ...one,
  instruments: [
    ...one.instruments,
    { channels, end_s: 80, instrument: "daq.1", rate_hz: 25, rows: 2000, start_s: 10 },
  ],
};

function windowOf(instrument: string, kind: "envelope" | "raw"): DaqWindow {
  return {
    instrument,
    origin: one.origin ?? 0,
    segments: [
      {
        channels,
        kind,
        points: kind === "raw" ? [[1, 0.5]] : [[1, 0.1, 0.9]],
        rate_hz: 2000,
      },
    ],
  };
}

function renderRecording(events: UpsetEntry[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DaqRecording events={events} runId="RUN-0001" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getDaqRecording.mockResolvedValue(one);
  getDaqWindow.mockImplementation((_run: string, instrument: string) =>
    Promise.resolve(windowOf(instrument, "envelope"))
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("DaqRecording", () => {
  it("says what was recorded", async () => {
    renderRecording();
    expect(
      await screen.findByText(/200,000 scans at 2,000 per second over 1m 40s/)
    ).toBeInTheDocument();
  });

  it("starts with the whole run", async () => {
    renderRecording();
    await waitFor(() =>
      expect(getDaqWindow).toHaveBeenCalledWith("RUN-0001", "daq.0", 0, 100, 2000)
    );
  });

  it("says when a view is thinned, and what to do about it", async () => {
    renderRecording();
    expect(
      await screen.findByText(/lowest and highest reading in each stretch/)
    ).toBeInTheDocument();
    expect(screen.getByText(/Drag across the graph to zoom in/)).toBeInTheDocument();
  });

  it("says when every scan is shown", async () => {
    getDaqWindow.mockImplementation((_run: string, instrument: string) =>
      Promise.resolve(windowOf(instrument, "raw"))
    );
    renderRecording();
    expect(await screen.findByText(/, every scan\./)).toBeInTheDocument();
  });

  it("zooms in on the middle of the view, and out again", async () => {
    renderRecording();
    const zoomIn = await screen.findByRole("button", { name: "Zoom in" });
    await userEvent.click(zoomIn);
    await waitFor(() =>
      expect(getDaqWindow).toHaveBeenCalledWith("RUN-0001", "daq.0", 25, 75, 2000)
    );
    await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    await waitFor(() =>
      expect(getDaqWindow).toHaveBeenLastCalledWith("RUN-0001", "daq.0", 0, 100, 2000)
    );
  });

  it("moves along the run and goes back to all of it", async () => {
    renderRecording();
    await userEvent.click(await screen.findByRole("button", { name: "Zoom in" }));
    await userEvent.click(screen.getByRole("button", { name: "Later" }));
    await waitFor(() =>
      expect(getDaqWindow).toHaveBeenLastCalledWith("RUN-0001", "daq.0", 50, 100, 2000)
    );
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() =>
      expect(getDaqWindow).toHaveBeenLastCalledWith("RUN-0001", "daq.0", 0, 100, 2000)
    );
  });

  it("offers no way back while all of the run is showing", async () => {
    renderRecording();
    expect(await screen.findByRole("button", { name: "All" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Earlier" })).toBeDisabled();
  });

  it("lets a channel be left out", async () => {
    renderRecording();
    const button = await screen.findByRole("button", { name: "CH 1" });
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it("shows nothing for a run that recorded nothing", async () => {
    getDaqRecording.mockResolvedValue({ instruments: [], origin: null });
    const { container } = renderRecording();
    await waitFor(() => expect(getDaqRecording).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("gives each channel a graph of its own when asked, and stacks them again", async () => {
    getDaqRecording.mockResolvedValue({
      ...one,
      instruments: [
        {
          ...one.instruments[0],
          channels: [...channels, { key: "2", label: "CH 2", unit: "V" }],
        },
      ],
    });
    const both = [...channels, { key: "2", label: "CH 2", unit: "V" }];
    getDaqWindow.mockResolvedValue({
      instrument: "daq.0",
      origin: 1,
      segments: [{ channels: both, kind: "raw", points: [[1, 0.5, 0.25]], rate_hz: 2000 }],
    });
    const { container } = renderRecording();
    await screen.findByRole("button", { name: "CH 2" });
    expect(container.querySelectorAll(".daq-recording__panel")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Stacked" }));
    expect(container.querySelectorAll(".daq-recording__panel")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Separate" }));
    expect(container.querySelectorAll(".daq-recording__panel")).toHaveLength(2);
  });

  it("opens the DAQ view in a window from the row of the layout choice", async () => {
    getDaqRecording.mockResolvedValue(two);
    const onPopOut = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DaqRecording events={[]} onPopOut={onPopOut} runId="RUN-0001" />
      </QueryClientProvider>
    );
    const open = await screen.findByRole("button", { name: "Open in a window" });
    expect(open.parentElement).toBe(screen.getByRole("button", { name: "Stacked" }).parentElement);
    await userEvent.click(open);
    expect(onPopOut).toHaveBeenCalledTimes(1);
  });

  it("follows every instrument over the span of all of them, on a graph each to begin with", async () => {
    getDaqRecording.mockResolvedValue(two);
    const { container } = renderRecording();
    await waitFor(() =>
      expect(getDaqWindow).toHaveBeenCalledWith("RUN-0001", "daq.1", 0, 100, 2000)
    );
    expect(await screen.findByRole("button", { name: "daq.0 · CH 1" })).toBeInTheDocument();
    expect(container.querySelectorAll(".daq-recording__panel")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Stacked" }));
    expect(container.querySelectorAll(".daq-recording__panel")).toHaveLength(0);
  });
});

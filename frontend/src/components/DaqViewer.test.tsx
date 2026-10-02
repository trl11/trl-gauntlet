import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { UpsetSummary } from "@api/types";
import { runUpsets, upsetCaptureCsv, upsetTrace } from "../test/fixtures";
import DaqViewer from "./DaqViewer";

const getArtifactText = vi.fn();
const getUpsetTrace = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getArtifactText: (...args: unknown[]) => getArtifactText(...args),
    getUpsetTrace: (...args: unknown[]) => getUpsetTrace(...args),
  };
});

const RUN_ID = "RUN-0001";

function renderViewer(summary: UpsetSummary, options: { flashing?: boolean; live?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DaqViewer
        flashing={options.flashing ?? false}
        live={options.live ?? true}
        runId={RUN_ID}
        summary={summary}
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getArtifactText.mockResolvedValue(upsetCaptureCsv);
  getUpsetTrace.mockResolvedValue(upsetTrace);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("DaqViewer events", () => {
  it("lists each upset with its channel, limit and reading", () => {
    renderViewer(runUpsets, { live: false });
    expect(screen.getByRole("cell", { name: "CH 1" })).toBeInTheDocument();
    expect(screen.getByText(/high -0.0500 V/)).toBeInTheDocument();
    expect(screen.getByText("-0.0012 V")).toBeInTheDocument();
  });

  it("says so when nothing has crossed a limit", () => {
    renderViewer({ ...runUpsets, events: [] }, { live: false });
    expect(screen.getByText("No DAQ events yet.")).toBeInTheDocument();
  });

  it("counts towards stop_after when one is set", () => {
    renderViewer({ ...runUpsets, stop_after: 5 }, { live: false });
    expect(screen.getByText("1 of 5 events before the run stops.")).toBeInTheDocument();
  });

  it("says when the monitor stopped the run", () => {
    renderViewer({ ...runUpsets, stopped_run: true }, { live: false });
    expect(screen.getByRole("status")).toHaveTextContent("Stopped after 1 event.");
  });

  it("draws the captured window of the upset chosen", async () => {
    renderViewer(runUpsets, { live: false });
    await userEvent.click(screen.getByRole("button", { name: "1" }));
    await waitFor(() =>
      expect(getArtifactText).toHaveBeenCalledWith(RUN_ID, "upsets/upset_0001.csv")
    );
  });

  it("marks an upset whose window was cut short", () => {
    const [event] = runUpsets.events;
    renderViewer({ ...runUpsets, events: [{ ...event, truncated: true }] }, { live: false });
    expect(screen.getByText(/CH 1 \(cut short\)/)).toBeInTheDocument();
  });
});

const TWO = { ...runUpsets, instruments: ["daq.0", "daq.1"] };

describe("DaqViewer live trace", () => {
  it("asks the instrument the run is watching for scans from the start", async () => {
    renderViewer(runUpsets);
    await waitFor(() =>
      expect(getUpsetTrace).toHaveBeenCalledWith(RUN_ID, "daq.0", 1, {
        displayHz: 50,
        tailS: 30,
      })
    );
  });

  it("shows none of the limits or the capture window, which are set when the run starts", async () => {
    renderViewer(runUpsets);
    await screen.findByRole("button", { name: "CH 1" });
    expect(screen.queryByLabelText(/limit$/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Before (s)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("After (s)")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
  });

  it("asks for nothing once the run is over", () => {
    renderViewer(runUpsets, { live: false });
    expect(getUpsetTrace).not.toHaveBeenCalled();
  });

  it("offers the choice of layout once there is more than one channel to arrange", async () => {
    renderViewer(runUpsets);
    expect(await screen.findByRole("button", { name: "Stacked" })).toBeInTheDocument();
  });

  it("offers none for a single channel", async () => {
    getUpsetTrace.mockResolvedValue({
      ...upsetTrace,
      channels: upsetTrace.channels.slice(0, 1),
      scans: upsetTrace.scans.map(([seq, at, values]) => [seq, at, values.slice(0, 1)]),
    });
    renderViewer(runUpsets);
    await screen.findByRole("button", { name: "CH 1" });
    expect(screen.queryByRole("button", { name: "Stacked" })).not.toBeInTheDocument();
  });

  it("follows every instrument the run watches in one view", async () => {
    renderViewer(TWO);
    await waitFor(() => {
      expect(getUpsetTrace).toHaveBeenCalledWith(RUN_ID, "daq.0", 1, expect.anything());
      expect(getUpsetTrace).toHaveBeenCalledWith(RUN_ID, "daq.1", 1, expect.anything());
    });
  });

  it("names each channel by its instrument when there are two", async () => {
    renderViewer(TWO);
    expect(await screen.findByRole("button", { name: "daq.0 · CH 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "daq.1 · CH 1" })).toBeInTheDocument();
  });

  it("starts with one graph for all of them when there are many channels", async () => {
    const { container } = renderViewer(TWO);
    await screen.findByRole("button", { name: "daq.0 · CH 1" });
    expect(container.querySelectorAll(".daq-viewer__trace")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Stacked" })).toHaveAttribute("aria-pressed", "true");
  });

  it("starts with a graph of its own for each channel when there are few", async () => {
    const two = {
      ...upsetTrace,
      channels: upsetTrace.channels.slice(0, 2),
      scans: upsetTrace.scans.map(([seq, at, values]) => [seq, at, values.slice(0, 2)]),
    };
    getUpsetTrace.mockResolvedValue(two);
    const { container } = renderViewer(runUpsets);
    await screen.findByRole("button", { name: "CH 2" });
    expect(container.querySelectorAll(".daq-viewer__trace")).toHaveLength(2);
  });

  it("gives every channel of every instrument a graph of its own when asked", async () => {
    const { container } = renderViewer(TWO);
    await screen.findByRole("button", { name: "daq.0 · CH 1" });
    await userEvent.click(screen.getByRole("button", { name: "Separate" }));
    expect(container.querySelectorAll(".daq-viewer__trace")).toHaveLength(16);
    expect(screen.getByRole("button", { name: "Separate" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("leaves a channel that is switched off without a graph", async () => {
    const { container } = renderViewer(TWO);
    await screen.findByRole("button", { name: "daq.0 · CH 1" });
    await userEvent.click(screen.getByRole("button", { name: "Separate" }));
    await userEvent.click(screen.getByRole("button", { name: "daq.0 · CH 1" }));
    expect(container.querySelectorAll(".daq-viewer__trace")).toHaveLength(15);
  });

  it("can stack them on one graph again", async () => {
    const { container } = renderViewer(TWO);
    await screen.findByRole("button", { name: "daq.0 · CH 1" });
    await userEvent.click(screen.getByRole("button", { name: "Separate" }));
    await userEvent.click(screen.getByRole("button", { name: "Stacked" }));
    expect(container.querySelectorAll(".daq-viewer__trace")).toHaveLength(1);
  });

  it("flashes the trace when told an event has just arrived", async () => {
    const { container } = renderViewer(runUpsets, { flashing: true });
    await screen.findByRole("button", { name: "CH 1" });
    expect(container.querySelector(".daq-viewer__trace--flash")).not.toBeNull();
  });

  it("does not flash otherwise", async () => {
    const { container } = renderViewer(runUpsets);
    await screen.findByRole("button", { name: "CH 1" });
    expect(container.querySelector(".daq-viewer__trace--flash")).toBeNull();
  });

  it("stops asking an instrument that refused, such as one whose run has just ended", async () => {
    getUpsetTrace.mockRejectedValue(new Error("run is not in flight"));
    renderViewer(runUpsets);
    await screen.findByText("Not streaming");
    await new Promise((resolve) => setTimeout(resolve, 700));
    expect(getUpsetTrace).toHaveBeenCalledTimes(1);
  });

  it("says so when an instrument is not delivering scans", async () => {
    getUpsetTrace.mockRejectedValue(new Error("not found"));
    renderViewer(runUpsets);
    expect(await screen.findByText("Not streaming")).toBeInTheDocument();
  });
});

describe("DaqViewer pop out", () => {
  it("offers a window of its own only when it can be opened", () => {
    renderViewer(runUpsets, { live: false });
    expect(screen.queryByRole("button", { name: "Open in a window" })).not.toBeInTheDocument();
  });

  it("asks for it from a key on the row of the layout choice", async () => {
    const onPopOut = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <DaqViewer flashing={false} live onPopOut={onPopOut} runId={RUN_ID} summary={runUpsets} />
      </QueryClientProvider>
    );
    const open = await screen.findByRole("button", { name: "Open in a window" });
    expect(open.parentElement).toBe(screen.getByRole("button", { name: "Stacked" }).parentElement);
    await userEvent.click(open);
    expect(onPopOut).toHaveBeenCalledTimes(1);
  });
});

describe("DaqViewer channels and zoom", () => {
  it("lets a channel be left out of the live trace", async () => {
    renderViewer(runUpsets);
    const button = await screen.findByRole("button", { name: "CH 3" });
    expect(button).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "true");
  });

  it("opens a finished run on its latest event, with nothing but the viewer", async () => {
    renderViewer(runUpsets, { live: false });
    await waitFor(() =>
      expect(getArtifactText).toHaveBeenCalledWith(RUN_ID, "upsets/upset_0001.csv")
    );
    expect(await screen.findByRole("button", { name: "Reset zoom" })).toBeDisabled();
    expect(
      screen.getByText(/Drag the bar beneath the chart to zoom and scroll/)
    ).toBeInTheDocument();
  });

  it("lets a channel be left out of an event's window", async () => {
    renderViewer(runUpsets, { live: false });
    const button = await screen.findByRole("button", { name: "CH 4" });
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-pressed", "false");
  });
});

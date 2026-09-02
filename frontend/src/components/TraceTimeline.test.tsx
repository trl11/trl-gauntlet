import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getArtifactText } from "@api/client";
import TraceTimeline from "./TraceTimeline";

vi.mock("@api/client", () => ({
  artifactUrl: (runId: string, path: string) => `/api/runs/${runId}/artifacts/${path}`,
  getArtifactText: vi.fn(),
}));

const captures = vi.mocked(getArtifactText);

/** Sample bytes as base64, the way the artifact carries them. */
function encode(bytes: number[]): string {
  return btoa(String.fromCharCode(...bytes));
}

/** A run of `count` captures a second apart, each a millisecond long. */
function file(count: number): string {
  const lines = [JSON.stringify({ channels: ["SCL", "SDA"], rate_hz: 1000 })];
  for (let index = 0; index < count; index += 1) {
    lines.push(
      JSON.stringify({
        elapsed_run_s: index,
        iteration: index,
        samples: 2,
        samples_base64: encode([index % 2 === 0 ? 0b11 : 0b00, 0b01]),
      })
    );
  }
  return lines.join("\n") + "\n";
}

/** jsdom has no canvas, so the draw is given somewhere to draw. */
function stubCanvas(): void {
  const context = {
    beginPath: vi.fn(),
    clearRect: vi.fn(),
    lineTo: vi.fn(),
    moveTo: vi.fn(),
    setTransform: vi.fn(),
    stroke: vi.fn(),
    lineWidth: 0,
    strokeStyle: "",
  };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    context as unknown as CanvasRenderingContext2D
  );
}

function renderTimeline() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TraceTimeline path="traces/captures.jsonl" runId="run-1" />
    </QueryClientProvider>
  );
}

describe("TraceTimeline", () => {
  beforeEach(() => {
    stubCanvas();
    captures.mockResolvedValue(file(5));
  });

  it("reads the one file the run appended to", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    expect(captures).toHaveBeenCalledWith("run-1", "traces/captures.jsonl");
  });

  it("labels one lane per channel", async () => {
    renderTimeline();
    expect(await screen.findByText("SCL")).toBeInTheDocument();
    expect(screen.getByText("SDA")).toBeInTheDocument();
  });

  it("says how many captures it holds and how long the run ran", async () => {
    renderTimeline();
    // Five captures a second apart, the last two samples long at 1 kHz.
    expect(await screen.findByText("5 captures over 4.002s")).toBeInTheDocument();
  });

  it("opens showing the whole run", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    expect(screen.getByLabelText("Range start, in seconds into the run")).toHaveValue("0.00000");
    expect(screen.getByLabelText("Range end, in seconds into the run")).toHaveValue("4.00200");
  });

  it("narrows the range to the time step that is picked", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    const step = screen.getByLabelText("Time per division");
    await userEvent.selectOptions(step, "0.1");
    // Ten divisions of 100ms is a one second window, from where it started.
    expect(screen.getByLabelText("Range end, in seconds into the run")).toHaveValue("1.00000");
  });

  it("scrolls to a start that is typed in, keeping the step", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    await userEvent.selectOptions(screen.getByLabelText("Time per division"), "0.1");
    const from = screen.getByLabelText("Range start, in seconds into the run");
    await userEvent.clear(from);
    await userEvent.type(from, "2{Enter}");
    // The one second window moved rather than stretching to reach.
    expect(from).toHaveValue("2.00000");
    expect(screen.getByLabelText("Range end, in seconds into the run")).toHaveValue("3.00000");
  });

  it("scrolls so a typed end is the right edge", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    await userEvent.selectOptions(screen.getByLabelText("Time per division"), "0.1");
    const to = screen.getByLabelText("Range end, in seconds into the run");
    await userEvent.clear(to);
    await userEvent.type(to, "3{Enter}");
    expect(screen.getByLabelText("Range start, in seconds into the run")).toHaveValue("2.00000");
  });

  it("will not scroll past the end of the run", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    await userEvent.selectOptions(screen.getByLabelText("Time per division"), "0.1");
    const from = screen.getByLabelText("Range start, in seconds into the run");
    await userEvent.clear(from);
    await userEvent.type(from, "99{Enter}");
    // The window stops with its right edge at the last capture.
    expect(from).toHaveValue("3.00200");
  });

  it("cannot be scrolled while the whole run is in view", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    const from = screen.getByLabelText("Range start, in seconds into the run");
    await userEvent.clear(from);
    await userEvent.type(from, "2{Enter}");
    expect(from).toHaveValue("0.00000");
  });

  it("names the iteration that recorded each capture", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    // Five captures a second apart across the whole run, so each is far
    // enough from the last to be named.
    expect(screen.getByText("Iteration")).toBeInTheDocument();
    for (const iteration of ["0", "1", "2", "3", "4"]) {
      expect(screen.getByText(iteration)).toBeInTheDocument();
    }
  });

  it("graduates the time axis, in the unit the view is read in", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    expect(screen.getByText("Time")).toBeInTheDocument();
    // Eleven divisions across four seconds, so the axis reads in seconds.
    expect(screen.getByText("0ns")).toBeInTheDocument();
    expect(screen.getByText("2.001s")).toBeInTheDocument();
    expect(screen.getByText("4.002s")).toBeInTheDocument();
  });

  it("reads the axis in microseconds once it is zoomed into one capture", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    await userEvent.selectOptions(screen.getByLabelText("Time per division"), "0.0001");
    expect(screen.getByText("500\u00b5s")).toBeInTheDocument();
  });

  it("offers a download of the file itself", async () => {
    renderTimeline();
    const link = await screen.findByLabelText("Download the captures");
    expect(link).toHaveAttribute("href", "/api/runs/run-1/artifacts/traces/captures.jsonl");
  });

  it("says so when the run has recorded nothing yet", async () => {
    captures.mockResolvedValue(JSON.stringify({ channels: ["SCL"], rate_hz: 1000 }) + "\n");
    renderTimeline();
    expect(await screen.findByText("No traces")).toBeInTheDocument();
  });

  it("says so when the file cannot be read", async () => {
    captures.mockRejectedValue(new Error("captures.jsonl not found"));
    renderTimeline();
    expect(await screen.findByText("Traces unavailable")).toBeInTheDocument();
  });
});

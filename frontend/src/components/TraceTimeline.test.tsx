import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
function stubCanvas() {
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
  return context;
}

/** jsdom lays nothing out, so the plot is given a width to draw across. */
function giveCanvasWidth(width: number): void {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(width);
}

const startField = () => screen.getByLabelText("Range start, in seconds into the run");
const endField = () => screen.getByLabelText("Range end, in seconds into the run");

function renderTimeline() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TraceTimeline path="traces/captures.jsonl" runId="run-1" />
    </QueryClientProvider>
  );
}

describe("TraceTimeline", () => {
  let context: ReturnType<typeof stubCanvas>;

  beforeEach(() => {
    context = stubCanvas();
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

  it("names a channel the file left unlabelled by its position", async () => {
    captures.mockResolvedValue(
      [
        JSON.stringify({ channels: ["", "SDA"], rate_hz: 1000 }),
        JSON.stringify({ elapsed_run_s: 0, iteration: 0, samples_base64: encode([1, 0]) }),
      ].join("\n")
    );
    renderTimeline();
    expect(await screen.findByText("CH 1")).toBeInTheDocument();
    expect(screen.getByText("SDA")).toBeInTheDocument();
  });

  it("zooms in around the pointer on a scroll up, and fits the run again on request", async () => {
    renderTimeline();
    const fit = await screen.findByRole("button", { name: "Fit the whole run" });
    expect(fit).toBeDisabled();

    // jsdom lays nothing out, so the pointer reads as the middle of the plot.
    fireEvent.wheel(screen.getByRole("img"), { deltaY: -100 });

    expect(startField()).toHaveValue("0.400200");
    expect(endField()).toHaveValue("3.60180");
    expect(fit).toBeEnabled();

    await userEvent.click(fit);

    expect(startField()).toHaveValue("0.00000");
    expect(endField()).toHaveValue("4.00200");
  });

  it("zooms no further out than the whole run", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);

    fireEvent.wheel(screen.getByRole("img"), { deltaY: 100 });

    expect(startField()).toHaveValue("0.00000");
    expect(endField()).toHaveValue("4.00200");
  });

  it("pans the window by dragging the plot", async () => {
    giveCanvasWidth(100);
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    renderTimeline();
    await screen.findByText(/5 captures/);
    await userEvent.selectOptions(screen.getByLabelText("Time per division"), "0.1");
    const plot = screen.getByRole("img");

    // jsdom has no PointerEvent, so the pointer is moved with mouse events
    // under the pointer events' names, which carry the position React reads.
    const pointer = (type: string, clientX: number) =>
      fireEvent(plot, new MouseEvent(type, { bubbles: true, clientX }));

    pointer("pointerdown", 80);
    expect(plot).toHaveClass("trace-timeline__canvas--panning");
    // Half the plot's width to the left is half the one second window later.
    pointer("pointermove", 30);
    pointer("pointerup", 30);

    expect(startField()).toHaveValue("0.500000");
    expect(plot).not.toHaveClass("trace-timeline__canvas--panning");

    // Once released, moving the pointer no longer drags.
    pointer("pointermove", 0);
    expect(startField()).toHaveValue("0.500000");
  });

  it("keeps the range as it was when what is typed is not a number", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    await userEvent.selectOptions(screen.getByLabelText("Time per division"), "0.1");

    await userEvent.clear(startField());
    await userEvent.type(startField(), "soon{Enter}");

    expect(startField()).toHaveValue("0.00000");
  });

  it("takes a typed start once the field loses focus", async () => {
    renderTimeline();
    await screen.findByText(/5 captures/);
    await userEvent.selectOptions(screen.getByLabelText("Time per division"), "0.1");

    await userEvent.clear(startField());
    await userEvent.type(startField(), "1.5");
    await userEvent.tab();

    expect(startField()).toHaveValue("1.50000");
  });

  it("draws an edge where a line changed level inside one column", async () => {
    giveCanvasWidth(200);
    renderTimeline();
    await waitFor(() => expect(context.stroke).toHaveBeenCalled());

    // SDA's lane spans y 41 to 61, and its first capture goes high then low,
    // both inside the first of 200 columns across four seconds.
    const moves = context.moveTo.mock.calls.map((call) => call.join(","));
    const lines = context.lineTo.mock.calls.map((call) => call.join(","));
    expect(moves).toContain("0.5,61");
    expect(lines).toContain("0.5,41");
  });
});

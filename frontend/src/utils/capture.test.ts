import { describe, expect, it } from "vitest";

import { decimate, parseCapture, type Capture } from "./capture";

const CSV = ["t_s,ch0,ch1", "0,0.0025,0.5", "4e-05,0.0026,0.51", "8e-05,0.0024,0.52"].join("\n");

function ramp(depth: number): Capture {
  return {
    channels: ["ch0"],
    times: Array.from({ length: depth }, (_, index) => index * 0.00004),
    values: [Array.from({ length: depth }, (_, index) => index)],
  };
}

describe("parseCapture", () => {
  it("reads the channels from the header and the samples under them", () => {
    const capture = parseCapture(CSV);
    expect(capture.channels).toEqual(["ch0", "ch1"]);
    expect(capture.times).toEqual([0, 0.00004, 0.00008]);
    expect(capture.values[0]).toEqual([0.0025, 0.0026, 0.0024]);
  });

  it("drops a row that will not parse rather than the whole file", () => {
    // A run killed mid-write leaves the last line half-written.
    const capture = parseCapture(`${CSV}\n0.00012,0.0025`);
    expect(capture.times).toHaveLength(3);
  });

  it("reads a file with no samples as empty", () => {
    expect(parseCapture("t_s,ch0").times).toEqual([]);
    expect(parseCapture("").channels).toEqual([]);
  });
});

describe("decimate", () => {
  it("returns every sample when the window fits the budget", () => {
    const points = decimate(ramp(50), 0, 49, 1200);
    expect(points).toHaveLength(50);
    expect(points[7].ch0).toBe(7);
  });

  it("thins a window that does not fit, keeping its extremes", () => {
    const points = decimate(ramp(5000), 0, 4999, 1200);
    expect(points.length).toBeLessThanOrEqual(1200);
    // The lowest and the highest sample of the capture both survive, which is
    // the whole point of an envelope: a peak is what a waveform is read by.
    const drawn = points.map((point) => point.ch0);
    expect(Math.min(...drawn)).toBe(0);
    expect(Math.max(...drawn)).toBe(4999);
  });

  it("draws a zoomed window sample for sample", () => {
    const points = decimate(ramp(5000), 100, 140, 1200);
    expect(points).toHaveLength(41);
    expect(points[0].ch0).toBe(100);
    expect(points[40].ch0).toBe(140);
  });

  it("keeps every channel on the same instants", () => {
    const capture: Capture = {
      channels: ["ch0", "ch1"],
      times: [0, 1, 2, 3],
      values: [
        [0, 5, 1, 4],
        [9, 8, 7, 6],
      ],
    };
    const points = decimate(capture, 0, 3, 2);
    // One point is one moment on every trace, so ch1 is read at the same
    // sample ch0's extreme was found at.
    for (const point of points) {
      const at = capture.times.indexOf(point.t);
      expect(point.ch1).toBe(capture.values[1][at]);
    }
  });
});

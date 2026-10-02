import { describe, expect, it } from "vitest";

import { decimate, parseCapture, windowDomain, type Capture } from "./capture";

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

  it("drops a row whose time or reading is not a number", () => {
    const capture = parseCapture(`${CSV}\nlate,0.0025,0.5\n0.00016,NaN,0.5`);
    expect(capture.times).toEqual([0, 0.00004, 0.00008]);
  });

  it("drops a half-written last row rather than reading its empty cell as zero", () => {
    const capture = parseCapture(`${CSV}\n0.00012,0.0027,\n0.00016, ,0.5`);
    expect(capture.times).toEqual([0, 0.00004, 0.00008]);
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

  it("reads a capture with no samples as nothing to draw", () => {
    expect(decimate({ channels: ["ch0"], times: [], values: [[]] }, 0, 10, 100)).toEqual([]);
  });

  it("clamps a window reaching past either end of the capture", () => {
    const points = decimate(ramp(10), -5, 50, 1200);
    expect(points.map((point) => point.ch0)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("keeps a falling bucket's extremes in the order they occurred", () => {
    const falling: Capture = {
      channels: ["ch0"],
      times: [0, 1, 2, 3, 4, 5],
      values: [[9, 8, 7, 3, 2, 1]],
    };
    const points = decimate(falling, 0, 5, 2);
    expect(points).toEqual([
      { ch0: 9, t: 0 },
      { ch0: 1, t: 5 },
    ]);
  });

  it("draws the times alone for a capture with no channels", () => {
    const bare: Capture = { channels: [], times: [0, 1, 2, 3], values: [] };
    expect(decimate(bare, 0, 3, 2)).toEqual([{ t: 0 }, { t: 0 }]);
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

describe("windowDomain", () => {
  it("spans everything that was asked for, however little of it was captured", () => {
    expect(windowDomain({ post_s: 2, pre_s: 0.5 }, null, [])).toEqual([-0.5, 2]);
  });

  it("is exactly the window that was zoomed to", () => {
    expect(windowDomain({ post_s: 2, pre_s: 0.5 }, [1, 3], [-0.5, -0.4, 0, 0.2, 1])).toEqual([
      -0.4, 0.2,
    ]);
  });
});

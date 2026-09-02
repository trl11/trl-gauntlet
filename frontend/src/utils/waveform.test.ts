import { describe, expect, it } from "vitest";

import {
  decodeSamples,
  extentOf,
  formatSeconds,
  laneLevels,
  axisTicks,
  captureMarks,
  parseCaptures,
  timelineMasks,
} from "./waveform";

/** Sample bytes as base64, the way the artifact carries them. */
function encode(bytes: number[]): string {
  return btoa(String.fromCharCode(...bytes));
}

describe("decodeSamples", () => {
  it("reads one byte per sample", () => {
    expect([...decodeSamples(encode([0x00, 0xff, 0xa5]))]).toEqual([0x00, 0xff, 0xa5]);
  });

  it("reads an empty capture as no samples", () => {
    expect(decodeSamples("")).toHaveLength(0);
  });
});

describe("formatSeconds", () => {
  it("names a time in the unit that suits it", () => {
    expect(formatSeconds(1.5)).toBe("1.5s");
    expect(formatSeconds(0.001)).toBe("1ms");
    expect(formatSeconds(8e-6)).toBe("8µs");
    expect(formatSeconds(4e-8)).toBe("40ns");
  });

  it("does not round a capture's shortest span away to zero", () => {
    // The shared duration formatter reports this as "0ms", which is what this
    // one exists to avoid.
    expect(formatSeconds(8e-6)).not.toMatch(/^0/);
  });
});

/** A capture of `count` samples all holding `byte`, starting at `startS`. */
function island(startS: number, byte: number, count = 4, rateHz = 1000, iteration = 0) {
  return { bytes: new Uint8Array(count).fill(byte), iteration, rateHz, startS };
}

describe("parseCaptures", () => {
  it("reads the header and every capture after it", () => {
    const text = [
      JSON.stringify({ channels: ["A", "B"], rate_hz: 1000 }),
      JSON.stringify({ elapsed_run_s: 0, iteration: 0, samples_base64: encode([1, 1]) }),
      JSON.stringify({ elapsed_run_s: 2.5, iteration: 1, samples_base64: encode([0, 0]) }),
      "",
    ].join("\n");
    const captures = parseCaptures(text);
    expect(captures.channels).toEqual(["A", "B"]);
    expect(captures.islands).toHaveLength(2);
    expect(captures.islands[1].startS).toBe(2.5);
    expect(captures.islands[0].rateHz).toBe(1000);
  });

  it("keeps the captures it could read when the last line is half written", () => {
    // The suite appends during the run, so a run that died mid-write leaves
    // exactly this, and the operator still wants the captures before it.
    const text = [
      JSON.stringify({ channels: ["A"], rate_hz: 1000 }),
      JSON.stringify({ elapsed_run_s: 0, samples_base64: encode([1]) }),
      '{"elapsed_run_s": 1.0, "samples_base',
    ].join("\n");
    expect(parseCaptures(text).islands).toHaveLength(1);
  });

  it("reads a file holding only its header as no captures", () => {
    expect(parseCaptures(JSON.stringify({ channels: ["A"], rate_hz: 1 })).islands).toEqual([]);
  });

  it("reads an empty file as nothing", () => {
    expect(parseCaptures("")).toEqual({ channels: [], islands: [] });
  });
});

describe("extentOf", () => {
  it("spans the first capture to the end of the last", () => {
    const extent = extentOf([island(0, 1), island(10, 1)]);
    expect(extent.startS).toBe(0);
    expect(extent.spanS).toBeCloseTo(10.004);
  });

  it("has no span with no captures", () => {
    expect(extentOf([])).toEqual({ startS: 0, spanS: 0 });
  });
});

describe("timelineMasks", () => {
  it("marks a column holding no capture as absent", () => {
    // Two captures a long way apart: the column between them must not claim
    // the line was low, because nothing was watching it there.
    const islands = [island(0, 0b0000_0001), island(10, 0b0000_0001)];
    const { present } = timelineMasks(islands, { startS: 0, spanS: 12 }, 12);
    expect(present[0]).toBe(1);
    expect(present[5]).toBe(0);
    expect(present[10]).toBe(1);
  });

  it("reports the level a column held where a capture covers it", () => {
    const { high, low, present } = timelineMasks(
      [island(0, 0b0000_0001)],
      { startS: 0, spanS: 0.004 },
      4
    );
    expect(present[0]).toBe(1);
    expect(high[0] & 1).toBe(1);
    expect(low[0] & 1).toBe(0);
  });

  it("reports both levels for a column covering an edge", () => {
    const islands = [
      { bytes: new Uint8Array([1, 0, 1, 0]), iteration: 0, rateHz: 1000, startS: 0 },
    ];
    const { high, low } = timelineMasks(islands, { startS: 0, spanS: 0.004 }, 1);
    expect(high[0] & 1).toBe(1);
    expect(low[0] & 1).toBe(1);
  });

  it("still shows a sample when zoomed in past one sample per column", () => {
    // 4 samples across 40 columns: each column is a tenth of a sample, and a
    // column landing inside the capture must draw the sample it is inside.
    const { present } = timelineMasks([island(0, 0xff)], { startS: 0, spanS: 0.004 }, 40);
    expect([...present].every((column) => column === 1)).toBe(true);
  });

  it("keeps the channels apart", () => {
    const { high } = timelineMasks([island(0, 0b1000_0000)], { startS: 0, spanS: 0.004 }, 4);
    expect((high[0] >> 7) & 1).toBe(1);
    expect(high[0] & 1).toBe(0);
  });
});

describe("laneLevels", () => {
  /** Two captures a gap apart, the first holding `a` and the second `b`. */
  function pair(a: number, b: number) {
    const islands = [
      { bytes: new Uint8Array(4).fill(a), iteration: 0, rateHz: 1000, startS: 0 },
      { bytes: new Uint8Array(4).fill(b), iteration: 1, rateHz: 1000, startS: 10 },
    ];
    const masks = timelineMasks(islands, { startS: 0, spanS: 10.004 }, 12);
    return { masks, lane: laneLevels(masks, 0, 12) };
  }

  it("holds the level of the last capture across the gap to the next", () => {
    const { lane } = pair(0b1, 0b1);
    expect(lane.levels[0]).toBe(1);
    expect(lane.levels[5]).toBe(1);
    expect(lane.observed[5]).toBe(0);
  });

  it("marks which columns a capture actually covered", () => {
    const { lane } = pair(0b1, 0b1);
    expect(lane.observed[0]).toBe(1);
    expect(lane.observed[5]).toBe(0);
  });

  it("changes level at the capture that saw the change, not before it", () => {
    // High, then low two captures later: the gap holds high the whole way and
    // the transition lands where the second capture starts.
    const { lane } = pair(0b1, 0b0);
    expect(lane.levels[5]).toBe(1);
    expect(lane.levels[11]).toBe(0);
  });

  it("draws nothing before the first capture", () => {
    const islands = [{ bytes: new Uint8Array([1]), iteration: 0, rateHz: 1000, startS: 5 }];
    const lane = laneLevels(timelineMasks(islands, { startS: 0, spanS: 6 }, 6), 0, 6);
    expect(lane.levels[0]).toBe(-1);
    expect(lane.levels[5]).toBe(1);
  });

  it("carries the level a capture ended on, not the one it started on", () => {
    // One column covering a falling edge ends low, so the gap after it is low.
    const islands = [
      { bytes: new Uint8Array([1, 1, 0, 0]), iteration: 0, rateHz: 1000, startS: 0 },
    ];
    const masks = timelineMasks(islands, { startS: 0, spanS: 0.008 }, 2);
    const lane = laneLevels(masks, 0, 2);
    expect(lane.levels[0]).toBe(2);
    expect(lane.ends[0]).toBe(0);
    expect(lane.levels[1]).toBe(0);
  });

  it("keeps the channels apart", () => {
    const islands = [
      { bytes: new Uint8Array([0b1000_0001]), iteration: 0, rateHz: 1000, startS: 0 },
    ];
    const masks = timelineMasks(islands, { startS: 0, spanS: 0.002 }, 2);
    expect(laneLevels(masks, 0, 2).levels[0]).toBe(1);
    expect(laneLevels(masks, 1, 2).levels[0]).toBe(0);
    expect(laneLevels(masks, 7, 2).levels[0]).toBe(1);
  });
});

describe("axisTicks", () => {
  it("names every division from the left edge to the right", () => {
    const ticks = axisTicks({ startS: 0, spanS: 1 }, 10);
    expect(ticks).toHaveLength(11);
    expect(ticks[0]).toEqual({ fraction: 0, label: "0ns" });
    expect(ticks[10]).toEqual({ fraction: 1, label: "1s" });
  });

  it("carries the unit that suits the span", () => {
    // A view a few milliseconds wide is read in milliseconds, not in seconds
    // to four decimal places.
    expect(axisTicks({ startS: 0, spanS: 0.005 }, 10)[2].label).toBe("1ms");
    expect(axisTicks({ startS: 0, spanS: 0.00001 }, 10)[5].label).toBe("5\u00b5s");
  });

  it("counts from where the view starts, not from the run", () => {
    expect(axisTicks({ startS: 2, spanS: 1 }, 10)[0].label).toBe("2s");
  });
});

describe("captureMarks", () => {
  it("places a capture at the fraction of the width it starts at", () => {
    const islands = [island(0, 1, 4, 1000, 1), island(5, 1, 4, 1000, 2)];
    expect(captureMarks(islands, { startS: 0, spanS: 10 }, 0)).toEqual([
      { fraction: 0, iteration: 1 },
      { fraction: 0.5, iteration: 2 },
    ]);
  });

  it("leaves out the captures outside the view", () => {
    const islands = [island(0, 1, 4, 1000, 1), island(50, 1, 4, 1000, 2)];
    const marks = captureMarks(islands, { startS: 0, spanS: 10 }, 0);
    expect(marks.map((mark) => mark.iteration)).toEqual([1]);
  });

  it("drops a capture that would be named on top of the one before it", () => {
    const islands = [
      island(0, 1, 4, 1000, 1),
      island(0.1, 1, 4, 1000, 2),
      island(5, 1, 4, 1000, 3),
    ];
    const marks = captureMarks(islands, { startS: 0, spanS: 10 }, 0.06);
    expect(marks.map((mark) => mark.iteration)).toEqual([1, 3]);
  });
});

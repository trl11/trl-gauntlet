import { describe, expect, it } from "vitest";

import type { RecordedTick } from "@api/types";

import { traceToSamples } from "./instrument_trace";

describe("traceToSamples", () => {
  it("merges instruments read on the same tick into one row", () => {
    const ticks: RecordedTick[] = [
      {
        at: "2026-09-11T18:09:20Z",
        instrument: "daq",
        t: 0,
        values: { "channels.ai0.value": 0.5 },
      },
      { at: "2026-09-11T18:09:20Z", instrument: "psu", t: 0, values: { voltage: 32 } },
      {
        at: "2026-09-11T18:09:21Z",
        instrument: "daq",
        t: 1,
        values: { "channels.ai0.value": 0.6 },
      },
    ];

    const samples = traceToSamples(ticks);

    expect(samples).toHaveLength(2);
    expect(samples[0].elapsed_s).toBe(0);
    expect(samples[0].values).toEqual({ "daq.channels.ai0.value": 0.5, "psu.voltage": 32 });
    expect(samples[1].elapsed_s).toBe(1);
    expect(samples[1].values).toEqual({ "daq.channels.ai0.value": 0.6 });
  });

  it("orders rows by elapsed time, whatever order the lines arrived in", () => {
    const ticks: RecordedTick[] = [
      { at: "2026-09-11T18:09:21Z", instrument: "daq", t: 1, values: { v: 2 } },
      { at: "2026-09-11T18:09:20Z", instrument: "daq", t: 0, values: { v: 1 } },
    ];

    const samples = traceToSamples(ticks);

    expect(samples.map((sample) => sample.elapsed_s)).toEqual([0, 1]);
  });
});

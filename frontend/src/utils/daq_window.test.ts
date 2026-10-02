import { describe, expect, it } from "vitest";

import type { DaqWindow } from "@api/types";
import { fitView, seriesOf } from "./daq_window";

const channels = [
  { key: "1", label: "Rail", unit: "V" },
  { key: "2", label: "Aux", unit: "V" },
];

const raw: DaqWindow = {
  instrument: "daq.0",
  origin: 100,
  segments: [
    {
      channels,
      kind: "raw",
      points: [
        [0, 1, -1],
        [0.5, 2, Number.NaN],
      ],
      rate_hz: 100,
    },
  ],
};

describe("seriesOf", () => {
  it("makes a line of each channel and leaves out a reading that was not given", () => {
    const [rail, aux] = seriesOf(raw, (label) => label);
    expect(rail.points).toEqual([
      { t: 0, v: 1 },
      { t: 0.5, v: 2 },
    ]);
    expect(aux.points).toEqual([{ t: 0, v: -1 }]);
    expect(rail.band).toBeUndefined();
  });

  it("makes a band of the highest readings when the window is an envelope", () => {
    const window: DaqWindow = {
      ...raw,
      segments: [{ ...raw.segments[0], kind: "envelope", points: [[1, 0, -5, 3, -2]] }],
    };
    const [rail, aux] = seriesOf(window, (label) => label);
    expect(rail.points).toEqual([{ t: 1, v: 0 }]);
    expect(rail.band).toEqual([{ t: 1, v: 3 }]);
    expect(aux.points).toEqual([{ t: 1, v: -5 }]);
    expect(aux.band).toEqual([{ t: 1, v: -2 }]);
  });

  it("joins a channel across the stretches it was recorded in", () => {
    const window: DaqWindow = {
      ...raw,
      segments: [
        { ...raw.segments[0], channels: [channels[0]], points: [[0, 1]] },
        { ...raw.segments[0], channels: [channels[0]], points: [[9, 2]] },
      ],
    };
    expect(seriesOf(window, (label) => label)[0].points).toHaveLength(2);
  });

  it("names a line as asked, so two instruments can be told apart", () => {
    expect(seriesOf(raw, (label) => `daq.0 · ${label}`)[0].name).toBe("daq.0 · Rail");
  });
});

describe("fitView", () => {
  it("leaves a view inside the span as it is", () => {
    expect(fitView([2, 4], [0, 10])).toEqual([2, 4]);
  });

  it("slides a view that went past an end back inside, keeping its width", () => {
    expect(fitView([-1, 1], [0, 10])).toEqual([0, 2]);
    expect(fitView([9, 11], [0, 10])).toEqual([8, 10]);
  });

  it("holds a view no wider than the span", () => {
    expect(fitView([-5, 50], [0, 10])).toEqual([0, 10]);
  });
});

import { describe, expect, it } from "vitest";

import { NO_READING, readingText, readoutGroups, tickDecimals, toneFor, valueAt } from "./readouts";

const KEY = "channels.1.voltage";

function history(...values: number[]): Array<Record<string, number>> {
  return values.map((value) => ({ [KEY]: value }));
}

describe("valueAt", () => {
  it("follows a dotted path into the state", () => {
    expect(valueAt({ channels: { "1": { voltage: 4.94 } } }, KEY)).toBe(4.94);
  });

  it("gives up when the path leaves the object", () => {
    expect(valueAt({ channels: 3 }, KEY)).toBeUndefined();
  });
});

describe("readingText", () => {
  it("stands in for a reading the instrument did not report", () => {
    expect(readingText(undefined, 2)).toBe(NO_READING);
    expect(readingText(null, 2)).toBe(NO_READING);
  });

  it("reads a boolean as the line being on or off", () => {
    expect(readingText(true, null)).toBe("on");
    expect(readingText(false, null)).toBe("off");
  });

  it("rounds to the precision the provider asked for", () => {
    expect(readingText(4.9382, 2)).toBe("4.94");
  });

  it("passes anything else through as text", () => {
    expect(readingText("open", null)).toBe("open");
  });
});

describe("readoutGroups", () => {
  it("keeps the order the provider declared and splits headline from summary", () => {
    const groups = readoutGroups([
      { group: "Channel 1", key: "a", label: "A", precision: 1, role: "headline", unit: "V" },
      { group: "Channel 1", key: "b", label: "B", precision: 1, role: "summary", unit: "V" },
      { group: "Channel 2", key: "c", label: "C", precision: 1, role: "headline", unit: "V" },
    ]);
    expect(groups.map((group) => group.name)).toEqual(["Channel 1", "Channel 2"]);
    expect(groups[0].headline.map((entry) => entry.key)).toEqual(["a"]);
    expect(groups[0].summary.map((entry) => entry.key)).toEqual(["b"]);
  });
});

describe("tickDecimals", () => {
  it("gives a chamber holding a fraction of a degree enough decimals", () => {
    expect(tickDecimals(history(50.01, 50.05), [KEY])).toBe(3);
  });

  it("uses two decimals over a range of a few tenths", () => {
    expect(tickDecimals(history(0.2, 0.9), [KEY])).toBe(2);
  });

  it("keeps one decimal over a range of a few units", () => {
    expect(tickDecimals(history(1, 4.5), [KEY])).toBe(1);
  });

  it("drops the decimals on a wide range", () => {
    expect(tickDecimals(history(0, 12, 24), [KEY])).toBe(0);
  });

  it("falls back to one decimal for a reading that has not moved", () => {
    expect(tickDecimals(history(0, 0, 0), [KEY])).toBe(1);
  });

  it("falls back to one decimal when no sample carries the series", () => {
    expect(tickDecimals([{ other: 1 }], [KEY])).toBe(1);
  });
});

describe("toneFor", () => {
  it("cycles the lamp colours while there are no more readings than colours", () => {
    expect([0, 1, 2].map((at) => toneFor(at, 3))).toEqual(["green", "red", "amber"]);
  });

  it("lands a two-reading display on the first two colours", () => {
    expect([0, 1].map((at) => toneFor(at, 2))).toEqual(["green", "red"]);
  });

  it("burns a display of more readings than colours uniformly white", () => {
    // Eight channels of a kind: a repeating cycle would tell none of them
    // apart, so the colour is dropped rather than repeated.
    const tones = Array.from({ length: 8 }, (_, at) => toneFor(at, 8));
    expect(new Set(tones)).toEqual(new Set(["white"]));
  });
});

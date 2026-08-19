import { describe, expect, it } from "vitest";

import { NO_READING, readingText, readoutGroups, toneFor, toneOf, valueAt } from "./readouts";

const KEY = "channels.1.voltage";

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

  it("never groups, because the display draws a separator as the decimal dot", () => {
    expect(readingText(3840, null)).toBe("3840");
    expect(readingText(16588800, null)).toBe("16588800");
    expect(readingText(3840, 0)).toBe("3840");
  });
});

describe("readoutGroups leaves a viewer's own readings out", () => {
  it("skips a reading pinned to a viewer", () => {
    const groups = readoutGroups([
      { group: "A", key: "x", label: "X", precision: null, role: "headline", unit: "" },
      { group: "A", key: "n", label: "N", precision: null, role: "viewer", unit: "" },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].headline.map((entry) => entry.key)).toEqual(["x"]);
    expect(groups[0].summary).toHaveLength(0);
  });
});

describe("toneOf", () => {
  const entry = (tone?: string) => ({
    group: "",
    key: "a",
    label: "A",
    precision: null,
    role: "headline" as const,
    tone,
    unit: "",
  });

  it("burns the colour the reading declared", () => {
    expect(toneOf(entry("white"), 0, 3)).toBe("white");
    expect(toneOf(entry("amber"), 0, 3)).toBe("amber");
  });

  it("falls back to the colour the position gives it", () => {
    expect(toneOf(entry(), 0, 3)).toBe("green");
    expect(toneOf(entry(""), 1, 3)).toBe("red");
  });

  it("ignores a colour the display does not have", () => {
    expect(toneOf(entry("chartreuse"), 0, 3)).toBe("green");
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

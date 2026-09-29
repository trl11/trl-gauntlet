import { describe, expect, it } from "vitest";

import { groupSeriesNames, naturalCompare, paddedDomain } from "./metrics";

describe("naturalCompare", () => {
  it("sorts numeric suffixes the way a person reads them", () => {
    const names = ["cpu10", "cpu2", "cpu1", "cpu9"];
    expect([...names].sort(naturalCompare)).toEqual(["cpu1", "cpu2", "cpu9", "cpu10"]);
  });
});

describe("naturalCompare ties", () => {
  it("reads two identical names as equal", () => {
    expect(naturalCompare("rail.volts", "rail.volts")).toBe(0);
  });

  it("sorts a name before a longer one it begins", () => {
    expect(naturalCompare("cpu", "cpu1")).toBeLessThan(0);
    expect(naturalCompare("cpu1", "cpu")).toBeGreaterThan(0);
  });

  it("never reads two different names as equal", () => {
    expect(naturalCompare("a", "a0")).toBeLessThan(0);
    expect(naturalCompare("a0", "a")).toBeGreaterThan(0);
    expect(naturalCompare("cpu01", "cpu1")).not.toBe(0);
  });

  it("orders text chunks alphabetically", () => {
    expect(naturalCompare("disk1", "cpu1")).toBeGreaterThan(0);
  });
});

describe("groupSeriesNames", () => {
  it("groups by the part before the first dot", () => {
    const groups = groupSeriesNames([
      "cpu.per_core.cpu10",
      "cpu.per_core.cpu2",
      "memory.used_percent",
      "swap.used_percent",
    ]);
    expect(groups.map((g) => g.group)).toEqual(["cpu", "memory", "swap"]);
  });

  it("sorts names inside a group numerically", () => {
    const groups = groupSeriesNames(["cpu.per_core.cpu10", "cpu.per_core.cpu2"]);
    expect(groups[0].names).toEqual(["cpu.per_core.cpu2", "cpu.per_core.cpu10"]);
  });

  it("gives an unaliased name with no dot its own group", () => {
    const groups = groupSeriesNames(["elapsed_run_s"]);
    expect(groups).toEqual([{ group: "elapsed_run_s", names: ["elapsed_run_s"] }]);
  });

  it("folds aliased host stats into an existing group", () => {
    const groups = groupSeriesNames([
      "cpu.percent",
      "cpu_count",
      "context_switches_per_s",
      "uptime_s",
      "thermal_max_c",
    ]);
    expect(groups).toEqual([
      {
        group: "cpu",
        names: ["context_switches_per_s", "cpu.percent", "cpu_count", "uptime_s"],
      },
      { group: "thermal", names: ["thermal_max_c"] },
    ]);
  });

  it("folds window_s into an other group", () => {
    const groups = groupSeriesNames(["window_s"]);
    expect(groups).toEqual([{ group: "other", names: ["window_s"] }]);
  });
});

describe("paddedDomain", () => {
  it("leaves a tenth of the range as air at each end", () => {
    // Range 10, so the window runs from 1 below to 1 above.
    expect(paddedDomain([20, 30])).toEqual([19, 31]);
  });

  it("takes the air from the value itself when nothing moved", () => {
    // A flat series has no range, and an axis pinned to one value draws no
    // line at all.
    expect(paddedDomain([0.5, 0.5])).toEqual([0.45, 0.55]);
  });

  it("falls back to one when the flat value is zero", () => {
    expect(paddedDomain([0, 0])).toEqual([-1, 1]);
  });

  it("ignores what is not a number", () => {
    expect(paddedDomain([20, Number.NaN, 30])).toEqual([19, 31]);
  });

  it("gives nothing for no values at all, so the chart decides", () => {
    expect(paddedDomain([])).toBeUndefined();
  });
});

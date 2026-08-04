import { describe, expect, it } from "vitest";

import { groupSeriesNames, naturalCompare } from "./metrics";

describe("naturalCompare", () => {
  it("sorts numeric suffixes the way a person reads them", () => {
    const names = ["cpu10", "cpu2", "cpu1", "cpu9"];
    expect([...names].sort(naturalCompare)).toEqual(["cpu1", "cpu2", "cpu9", "cpu10"]);
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

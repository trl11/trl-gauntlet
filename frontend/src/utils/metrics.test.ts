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
      "uptime_s",
    ]);
    expect(groups.map((g) => g.group)).toEqual(["cpu", "memory", "uptime_s"]);
  });

  it("sorts names inside a group numerically", () => {
    const groups = groupSeriesNames(["cpu.per_core.cpu10", "cpu.per_core.cpu2"]);
    expect(groups[0].names).toEqual(["cpu.per_core.cpu2", "cpu.per_core.cpu10"]);
  });

  it("gives a name with no dot its own group", () => {
    const groups = groupSeriesNames(["uptime_s"]);
    expect(groups).toEqual([{ group: "uptime_s", names: ["uptime_s"] }]);
  });
});

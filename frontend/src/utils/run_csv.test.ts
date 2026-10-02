import { describe, expect, it } from "vitest";

import { run } from "../test/fixtures";

import { toCsv } from "./run_csv";

const HEADER =
  "run_id,suite,profile,status,verdict,unit_serial,target,started_at,ended_at,duration_s,fail_reason";

describe("toCsv", () => {
  it("writes only the header when no run is selected", () => {
    expect(toCsv([])).toBe(HEADER);
  });

  it("writes one row per run, empty where a field is null", () => {
    const row = {
      ...run,
      duration_s: null,
      ended_at: null,
      fail_reason: null,
      profile: "smoke",
      run_id: "R1",
      started_at: "2026-01-02T03:04:05Z",
      status: "running" as const,
      suite: "ssd",
      target: null,
      unit_serial: "SN-1",
      verdict: null,
    };

    expect(toCsv([row]).split("\n")[1]).toBe("R1,ssd,smoke,running,,SN-1,,2026-01-02T03:04:05Z,,,");
  });

  it("quotes a cell holding a comma, a quote or a newline, doubling its quotes", () => {
    const row = { ...run, fail_reason: 'rail "A" low,\nthen tripped' };

    expect(toCsv([row])).toContain('"rail ""A"" low,\nthen tripped"');
  });
});

import { describe, expect, it } from "vitest";

import { isLive, matchesStatus } from "./run_status";

describe("isLive", () => {
  it("is true for every in-flight status and nothing else", () => {
    for (const status of ["aborting", "running", "starting", "stopping"]) {
      expect(isLive(status)).toBe(true);
    }
    expect(isLive("passed")).toBe(false);
    expect(isLive(null)).toBe(false);
    expect(isLive(undefined)).toBe(false);
  });
});

describe("matchesStatus", () => {
  it("lets every run through the any-status filter", () => {
    expect(matchesStatus("error", "all")).toBe(true);
    expect(matchesStatus(null, "all")).toBe(true);
  });

  it("treats the in-flight filter as any live status", () => {
    expect(matchesStatus("stopping", "live")).toBe(true);
    expect(matchesStatus("aborted", "live")).toBe(false);
  });

  it("matches any other filter exactly", () => {
    expect(matchesStatus("failed", "failed")).toBe(true);
    expect(matchesStatus("error", "failed")).toBe(false);
  });
});

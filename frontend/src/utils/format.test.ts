import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatDuration,
  formatNumber,
  formatPercent,
  formatRelativeTime,
  formatTimestamp,
  toDate,
} from "./format";

describe("formatDuration", () => {
  it("renders sub-second values as milliseconds", () => {
    expect(formatDuration(0.25)).toBe("250ms");
  });

  it("renders zero explicitly", () => {
    expect(formatDuration(0)).toBe("0s");
  });

  it("drops leading and trailing zero units but keeps mid zeros", () => {
    expect(formatDuration(3601)).toBe("1h 0m 1s");
    expect(formatDuration(3600)).toBe("1h");
    expect(formatDuration(90)).toBe("1m 30s");
    expect(formatDuration(90061)).toBe("1d 1h 1m 1s");
  });

  it("returns a dash for missing or nonsensical input", () => {
    expect(formatDuration(null)).toBe("-");
    expect(formatDuration(undefined)).toBe("-");
    expect(formatDuration(-1)).toBe("-");
    expect(formatDuration(Number.NaN)).toBe("-");
  });
});

describe("formatBytes", () => {
  it("keeps whole bytes unscaled", () => {
    expect(formatBytes(512)).toBe("512 B");
  });

  it("scales in binary steps", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1536, 1)).toBe("1.5 KB");
    expect(formatBytes(1024 ** 3)).toBe("1.0 GB");
  });

  it("keeps the sign of a negative delta", () => {
    expect(formatBytes(-2048)).toBe("-2.0 KB");
  });

  it("returns a dash for missing input", () => {
    expect(formatBytes(null)).toBe("-");
  });
});

describe("toDate", () => {
  it("reads epoch seconds and milliseconds", () => {
    expect(toDate(1767225600)?.toISOString()).toBe("2026-01-01T00:00:00.000Z");
    expect(toDate(1767225600000)?.toISOString()).toBe("2026-01-01T00:00:00.000Z");
  });

  it("rejects unparseable values", () => {
    expect(toDate("not a date")).toBeNull();
    expect(toDate("")).toBeNull();
    expect(toDate(null)).toBeNull();
  });
});

describe("formatTimestamp", () => {
  it("renders a parseable timestamp", () => {
    expect(formatTimestamp("2026-01-01T00:00:00Z")).not.toBe("-");
  });

  it("returns a dash for junk", () => {
    expect(formatTimestamp("nonsense")).toBe("-");
  });
});

describe("formatRelativeTime", () => {
  const now = new Date("2026-01-01T12:00:00Z");

  it("collapses the last few seconds", () => {
    expect(formatRelativeTime("2026-01-01T11:59:58Z", now)).toBe("just now");
  });

  it("counts backwards in the largest fitting unit", () => {
    expect(formatRelativeTime("2026-01-01T11:55:00Z", now)).toContain("minute");
    expect(formatRelativeTime("2026-01-01T09:00:00Z", now)).toContain("hour");
    expect(formatRelativeTime("2025-12-29T12:00:00Z", now)).toContain("day");
  });

  it("returns a dash for missing input", () => {
    expect(formatRelativeTime(null, now)).toBe("-");
  });
});

describe("formatNumber", () => {
  it("trims trailing zeros by default", () => {
    expect(formatNumber(1.5)).toBe("1.5");
    expect(formatNumber(2)).toBe("2");
  });

  it("honours a fixed precision", () => {
    expect(formatNumber(2, 2)).toBe("2.00");
  });

  it("returns a dash for missing input", () => {
    expect(formatNumber(undefined)).toBe("-");
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe("-");
  });
});

describe("formatPercent", () => {
  it("appends a percent sign at the requested precision", () => {
    expect(formatPercent(42.57)).toBe("42.6%");
    expect(formatPercent(42.55, 0)).toBe("43%");
  });

  it("returns a dash for missing input", () => {
    expect(formatPercent(null)).toBe("-");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import type { MetricsRecord } from "@api/types";

import { elapsedSeconds, parseLog, replay } from "./run_history";

describe("replay", () => {
  it("turns each record's numeric leaves into one sample, keyed by its index", () => {
    const records: MetricsRecord[] = [
      {
        elapsed_run_s: 1.5,
        iteration: 2,
        kind: "live",
        metrics: { label: "x", list: [1, 2], rail: { volts: 3.3, bad: Number.NaN } },
        timestamp: 100,
      },
    ];

    expect(replay(records).samples).toEqual([
      { elapsed_s: 1.5, iteration: 2, seq: 0, ts: 100, values: { "rail.volts": 3.3 } },
    ]);
  });

  it("records no sample for a record carrying no numbers", () => {
    expect(replay([{ metrics: { note: "hi" }, success: true, timestamp: 1 }]).samples).toEqual([]);
  });

  it("fills what a sparse record leaves out", () => {
    // A record as an older run wrote it, read the way the API hands it over.
    const record: MetricsRecord = JSON.parse('{"metrics": {"v": 1}}');
    const [sample] = replay([record]).samples;
    expect(sample).toEqual({ elapsed_s: null, iteration: null, seq: 0, ts: 0, values: { v: 1 } });
  });

  it("lists each phase against its record's iteration, successful unless it says otherwise", () => {
    const records: MetricsRecord[] = [
      {
        iteration: 4,
        phases: [
          { detail: { n: 1 }, elapsed_s: 0.2, error: null, name: "write", success: true },
          { detail: {}, elapsed_s: 0.1, error: "x", name: "verify", success: false },
        ],
        timestamp: 1,
      },
    ];

    expect(replay(records).phases).toEqual([
      { detail: { n: 1 }, elapsed_s: 0.2, iteration: 4, phase: "write", success: true },
      { detail: {}, elapsed_s: 0.1, iteration: 4, phase: "verify", success: false },
    ]);
  });

  it("fills a phase that recorded no detail or time", () => {
    const record: MetricsRecord = JSON.parse('{"phases": [{"name": "boot"}], "timestamp": 1}');

    expect(replay([record]).phases).toEqual([
      { detail: {}, elapsed_s: 0, iteration: null, phase: "boot", success: true },
    ]);
  });

  it("lists an anomaly and never counts it as an iteration", () => {
    const records: MetricsRecord[] = [
      { kind: "anomaly", timestamp: 9 },
      {
        anomaly_kind: "envelope",
        detail: { value: 5 },
        kind: "anomaly",
        probe: "rail",
        success: false,
        timestamp: 10,
      },
    ];

    const replayed = replay(records);

    expect(replayed.anomalies).toEqual([
      { anomaly_kind: "", detail: undefined, probe: "", seq: 0, ts: 9 },
      { anomaly_kind: "envelope", detail: { value: 5 }, probe: "rail", seq: 1, ts: 10 },
    ]);
    expect(replayed.iterations).toEqual([]);
  });

  it("counts an iteration only for a record with an outcome that is not live", () => {
    const records: MetricsRecord[] = [
      { kind: "live", success: true, timestamp: 1 },
      { success: null, timestamp: 2 },
      {
        elapsed_run_s: 3,
        iteration: 1,
        metrics: { images: ["a.png"], traces: ["t.csv"] },
        reason: "ok",
        success: true,
        timestamp: 3,
      },
      { metrics: { images: "a.png" }, success: false, timestamp: 4 },
    ];

    expect(replay(records).iterations).toEqual([
      {
        elapsed_run_s: 3,
        images: ["a.png"],
        iteration: 1,
        reason: "ok",
        success: true,
        traces: ["t.csv"],
        ts: 3,
      },
      {
        elapsed_run_s: null,
        images: [],
        iteration: null,
        reason: "",
        success: false,
        traces: [],
        ts: 4,
      },
    ]);
  });
});

describe("parseLog", () => {
  it("reads nothing from an empty log", () => {
    expect(parseLog("")).toEqual([]);
  });

  it("splits stamped lines into time and message, and grades each by its text", () => {
    const text = [
      "2026-01-02T03:04:05.000Z starting",
      "2026-01-02T03:04:06.500Z WARNING: slow",
      "Traceback (most recent call last):",
      "critical failure",
    ].join("\n");

    expect(parseLog(`${text}\n`)).toEqual([
      { level: "info", message: "starting", seq: 0, ts: Date.parse("2026-01-02T03:04:05Z") / 1000 },
      {
        level: "warning",
        message: "WARNING: slow",
        seq: 1,
        ts: Date.parse("2026-01-02T03:04:06.500Z") / 1000,
      },
      { level: "error", message: "Traceback (most recent call last):", seq: 2, ts: null },
      { level: "error", message: "critical failure", seq: 3, ts: null },
    ]);
  });
});

describe("elapsedSeconds", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses the recorded duration when there is one", () => {
    expect(elapsedSeconds("2026-01-01T00:00:00Z", null, 42)).toBe(42);
  });

  it("measures from start to end when no duration was recorded", () => {
    expect(elapsedSeconds("2026-01-01T00:00:00Z", "2026-01-01T00:01:30Z", null)).toBe(90);
  });

  it("measures to now for a run that has not ended", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:10Z"));

    expect(elapsedSeconds("2026-01-01T00:00:00Z", null, null)).toBe(10);
  });

  it("reads an unparseable start as no time at all", () => {
    expect(elapsedSeconds("not a date", null, null)).toBe(0);
  });
});

/**
 * Turning a finished run's stored artifacts into the rows the live SSE stream
 * produces, so the run view renders one shape either way.
 */

import type { LogLevel, MetricsRecord } from "@api/types";
import type { PhaseRow } from "@components/IterationMap";
import type { IterationRow } from "@components/IterationTable";
import type { LogLine } from "@components/LogStream";
import type { MetricSample } from "@components/MetricsChart";
import { toDate } from "./format";

/** A probe reading outside its envelope, live or replayed. */
export interface AnomalyRow {
  anomaly_kind: string;
  detail: unknown;
  probe: string;
  seq: number;
  ts: number;
}

/** Everything a finished run's `metrics.jsonl` yields. */
export interface Replayed {
  anomalies: AnomalyRow[];
  iterations: IterationRow[];
  phases: PhaseRow[];
  samples: MetricSample[];
}

/** Collect every finite numeric leaf under `value` as a dotted path. */
function flattenNumbers(value: unknown, prefix: string, into: Record<string, number>): void {
  if (typeof value === "number" && Number.isFinite(value)) {
    into[prefix] = value;
    return;
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) return;
  for (const [key, child] of Object.entries(value)) {
    flattenNumbers(child, prefix ? `${prefix}.${key}` : key, into);
  }
}

/**
 * Turn stored metrics records into the same rows the live stream produces.
 *
 * The record index stands in for the sequence number a live event carries, so
 * every list has a stable key either way.
 */
export function replay(records: MetricsRecord[]): Replayed {
  const result: Replayed = { anomalies: [], iterations: [], phases: [], samples: [] };
  records.forEach((record, index) => {
    const values: Record<string, number> = {};
    flattenNumbers(record.metrics, "", values);
    if (Object.keys(values).length > 0) {
      result.samples.push({
        elapsed_s: record.elapsed_run_s ?? null,
        iteration: record.iteration ?? null,
        seq: index,
        ts: record.timestamp ?? 0,
        values,
      });
    }
    for (const phase of record.phases ?? []) {
      result.phases.push({
        detail: phase.detail ?? {},
        elapsed_s: phase.elapsed_s ?? 0,
        iteration: record.iteration ?? null,
        phase: phase.name,
        success: phase.success !== false,
      });
    }
    if (record.kind === "anomaly") {
      result.anomalies.push({
        anomaly_kind: record.anomaly_kind ?? "",
        detail: record.detail,
        probe: record.probe ?? "",
        seq: index,
        ts: record.timestamp ?? 0,
      });
      return;
    }
    if (record.kind === "live" || record.success == null) return;
    const images = record.metrics?.images;
    result.iterations.push({
      elapsed_run_s: record.elapsed_run_s ?? null,
      images: Array.isArray(images) ? (images as string[]) : [],
      iteration: record.iteration ?? null,
      reason: record.reason ?? "",
      success: record.success === true,
    });
  });
  return result;
}

/** The severity a captured log line reads as. */
function levelOf(message: string): LogLevel {
  const upper = message.toUpperCase();
  if (upper.includes("ERROR") || upper.includes("CRITICAL") || upper.includes("TRACEBACK")) {
    return "error";
  }
  return upper.includes("WARN") ? "warning" : "info";
}

/** Read `test.log` as log lines. The captured file carries no timestamps. */
export function parseLog(text: string): LogLine[] {
  if (text === "") return [];
  return text
    .replace(/\n$/, "")
    .split("\n")
    .map((message, index) => ({ level: levelOf(message), message, seq: index, ts: null }));
}

/** Seconds the run has taken so far, or took in total. */
export function elapsedSeconds(
  startedAt: string,
  endedAt: string | null,
  duration: number | null
): number {
  if (duration != null) return duration;
  const start = toDate(startedAt);
  if (start === null) return 0;
  const end = toDate(endedAt) ?? new Date();
  return (end.getTime() - start.getTime()) / 1000;
}

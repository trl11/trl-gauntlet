import type { MetricSample } from "@components/MetricsChart";
import type { RecordedTick } from "@api/types";

/**
 * `instruments.jsonl`, reshaped into {@link MetricsChart}'s rows.
 *
 * Every instrument ticks on its own line, so two instruments read at the same
 * moment arrive as two lines sharing one `t`; this merges them into one row.
 * A reading is prefixed with the instrument it came from, since two
 * instruments may publish a key of the same name.
 */
export function traceToSamples(ticks: RecordedTick[]): MetricSample[] {
  const byTime = new Map<number, { ts: number; values: Record<string, number> }>();
  for (const tick of ticks) {
    const row = byTime.get(tick.t) ?? { ts: Date.parse(tick.at) / 1000, values: {} };
    for (const [key, value] of Object.entries(tick.values)) {
      row.values[`${tick.instrument}.${key}`] = value;
    }
    byTime.set(tick.t, row);
  }
  return [...byTime.entries()]
    .sort(([a], [b]) => a - b)
    .map(([t, { ts, values }], index) => ({
      elapsed_s: t,
      iteration: null,
      seq: index,
      ts,
      values,
    }));
}

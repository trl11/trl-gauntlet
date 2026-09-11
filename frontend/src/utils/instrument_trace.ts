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
  const byTime = new Map<number, Record<string, number>>();
  for (const tick of ticks) {
    const values = byTime.get(tick.t) ?? {};
    for (const [key, value] of Object.entries(tick.values)) {
      values[`${tick.instrument}.${key}`] = value;
    }
    byTime.set(tick.t, values);
  }
  return [...byTime.entries()]
    .sort(([a], [b]) => a - b)
    .map(([t, values], index) => ({ elapsed_s: t, iteration: null, seq: index, ts: t, values }));
}

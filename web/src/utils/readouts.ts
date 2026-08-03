/** Reading a provider's declared readouts against the state it reported. */

import type { InstrumentReadout } from "@api/types";
import { formatNumber } from "./format";

/** Stands in for a reading the instrument did not report. */
export const NO_READING = "—";

/** One section of readouts: the tiles, then the strip beneath them. */
export interface ReadoutGroup {
  headline: InstrumentReadout[];
  name: string;
  summary: InstrumentReadout[];
}

/** The value at a dotted path in an instrument's state. */
export function valueAt(state: Record<string, unknown>, key: string): unknown {
  let current: unknown = state;
  for (const part of key.split(".")) {
    if (current === null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

/** One reading as text, rounded to the precision the provider asked for. */
export function readingText(value: unknown, precision: number | null): string {
  if (value === null || value === undefined) return NO_READING;
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value !== "number") return String(value);
  return precision === null ? formatNumber(value) : value.toFixed(precision);
}

/** Readouts split into their groups, in the order the provider declared them. */
export function readoutGroups(readouts: InstrumentReadout[]): ReadoutGroup[] {
  const groups = new Map<string, ReadoutGroup>();
  for (const entry of readouts) {
    let group = groups.get(entry.group);
    if (group === undefined) {
      group = { headline: [], name: entry.group, summary: [] };
      groups.set(entry.group, group);
    }
    if (entry.role === "summary") group.summary.push(entry);
    else group.headline.push(entry);
  }
  return [...groups.values()];
}

/**
 * Decimals a chart axis needs so neighbouring ticks do not read alike.
 *
 * The narrower the values are spread, the more decimals it takes to tell one
 * tick from the next: a chamber holding to a hundredth of a degree needs
 * three, a supply swinging from 0 to 12 volts needs none. A reading that has
 * not moved at all gets one, because the axis it is drawn against is whatever
 * range the chart invented.
 */
export function tickDecimals(history: Array<Record<string, number>>, keys: string[]): number {
  const values: number[] = [];
  for (const sample of history) {
    for (const key of keys) {
      const value = sample[key];
      if (typeof value === "number") values.push(value);
    }
  }
  if (values.length === 0) return 1;
  const span = Math.max(...values) - Math.min(...values);
  if (span === 0) return 1;
  if (span >= 10) return 0;
  if (span >= 1) return 1;
  if (span >= 0.1) return 2;
  return 3;
}

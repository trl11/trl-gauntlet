/** Reading a provider's declared readouts against the state it reported. */

import type { InstrumentReadout } from "@api/types";

/** Stands in for a reading the instrument did not report. */
export const NO_READING = "—";

/** Lamp colours a display cycles through, in the order readouts arrive. */
const TONES = ["green", "red", "amber"] as const;

/** A colour a seven-bar display burns a reading in. */
export type ReadingTone = (typeof TONES)[number] | "white";

/** One section of readouts: what the display burns large, then the row beneath. */
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

/**
 * One reading as text, rounded to the precision the provider asked for.
 *
 * Never grouped. The reading is drawn on a seven segment display, which has
 * no separator glyph and turns one into the decimal dot, so a grouped 3840
 * would read as 3.840.
 */
export function readingText(value: unknown, precision: number | null): string {
  if (value === null || value === undefined) return NO_READING;
  if (typeof value === "boolean") return value ? "on" : "off";
  if (typeof value !== "number") return String(value);
  return precision === null ? String(Number(value.toFixed(3))) : value.toFixed(precision);
}

/**
 * The colour the reading at `index` burns, of `count` on one display.
 *
 * Colour is what tells one reading from the next, and it can only do that
 * while there are no more readings than there are colours: three lands a
 * supply on green volts, red amps and amber watts without anything knowing it
 * is a supply. Past that the cycle repeats, so the colour distinguishes
 * nothing and only reads as decoration — an eight-channel acquisition unit
 * would light two greens, three reds and three ambers across readings that are
 * all of a kind. A display holding more than the cycle burns them uniformly
 * instead, which is also how a multi-channel instrument's own front panel does
 * it.
 */
export function toneFor(index: number, count: number): ReadingTone {
  return count > TONES.length ? "white" : TONES[index % TONES.length];
}

/**
 * The colour a reading burns: the one it declared, or the one its position
 * gives it.
 *
 * A provider names a colour when the one a position would give says something
 * the value does not, such as a running total burning red beside the rate it
 * accumulates.
 */
export function toneOf(readout: InstrumentReadout, index: number, count: number): ReadingTone {
  const declared = ([...TONES, "white"] as const).find((tone) => tone === readout.tone);
  return declared ?? toneFor(index, count);
}

/**
 * Readouts split into their groups, in the order the provider declared them.
 *
 * A reading the provider pinned to a viewer is left out: it is drawn with that
 * viewer's controls rather than on a display of its own.
 */
export function readoutGroups(readouts: InstrumentReadout[]): ReadoutGroup[] {
  const groups = new Map<string, ReadoutGroup>();
  for (const entry of readouts) {
    if (entry.role === "viewer") continue;
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

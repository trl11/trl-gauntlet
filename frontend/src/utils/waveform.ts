/** Reading and reducing a run's captures, for the timeline that draws them. */

/** One byte per sample, bit *n* being channel *n + 1*. */
export function decodeSamples(encoded: string): Uint8Array {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

/**
 * A time on the axis, in the unit that suits it.
 *
 * The shared duration formatter rounds to whole milliseconds, which a capture
 * zoomed to a handful of samples is far below.
 */
export function formatSeconds(seconds: number): string {
  const scales: Array<[number, string]> = [
    [1, "s"],
    [1e-3, "ms"],
    [1e-6, "\u00b5s"],
  ];
  for (const [size, unit] of scales) {
    if (seconds >= size) {
      return `${Number((seconds / size).toPrecision(4))}${unit}`;
    }
  }
  return `${Math.round(seconds * 1e9)}ns`;
}

/** One capture, placed where it happened in the run. */
export interface Island {
  /** The samples, one byte each. */
  bytes: Uint8Array;
  /** Samples per second. */
  rateHz: number;
  /** Seconds into the run at which the capture starts. */
  startS: number;
}

/** A run's captures, as `traces/*.jsonl` carries them. */
export interface Captures {
  /** A label per channel, the first being channel 1. */
  channels: string[];
  /** Every capture, in the order the run recorded them. */
  islands: Island[];
}

/** Which stretch of the run the plot is showing. */
export interface TimeView {
  /** Seconds into the run at the left edge. */
  startS: number;
  /** How many seconds the width covers. */
  spanS: number;
}

/** Where a capture ends, in run seconds. */
export function endOf(island: Island): number {
  return island.startS + island.bytes.length / island.rateHz;
}

/**
 * A run's captures, read from the JSON Lines artifact.
 *
 * The first line carries the channel labels and the rate; every line after it
 * is one capture. A line that will not parse is skipped rather than failing
 * the lot, because the file is appended to during the run and the last line of
 * a run that died can be half-written.
 */
export function parseCaptures(text: string): Captures {
  const lines = text.split("\n").filter((line) => line.trim() !== "");
  if (lines.length === 0) return { channels: [], islands: [] };

  let channels: string[] = [];
  let rateHz = 0;
  try {
    const header = JSON.parse(lines[0]) as { channels?: string[]; rate_hz?: number };
    channels = header.channels ?? [];
    rateHz = header.rate_hz ?? 0;
  } catch {
    return { channels: [], islands: [] };
  }

  const islands: Island[] = [];
  for (const line of lines.slice(1)) {
    try {
      const row = JSON.parse(line) as { elapsed_run_s?: number; samples_base64?: string };
      if (typeof row.samples_base64 !== "string") continue;
      islands.push({
        bytes: decodeSamples(row.samples_base64),
        rateHz,
        startS: row.elapsed_run_s ?? 0,
      });
    } catch {
      continue;
    }
  }
  return { channels, islands };
}

/** The run seconds a set of captures covers, end to end. */
export function extentOf(islands: Island[]): TimeView {
  if (islands.length === 0) return { startS: 0, spanS: 0 };
  const startS = islands[0].startS;
  const end = Math.max(...islands.map(endOf));
  return { startS, spanS: Math.max(end - startS, 1e-9) };
}

/**
 * What each pixel column of a view holds, as three bitmasks per column.
 *
 * `present` is the one a single capture does not need: a run samples a window
 * at a time, so most of a zoomed-out timeline is between captures and holds no
 * level at all. Drawing that as a level would claim the line was low when
 * nothing was watching it.
 */
export function timelineMasks(
  islands: Island[],
  view: TimeView,
  columns: number
): { high: Uint8Array; last: Uint8Array; low: Uint8Array; present: Uint8Array } {
  const high = new Uint8Array(columns);
  const last = new Uint8Array(columns);
  const low = new Uint8Array(columns);
  const present = new Uint8Array(columns);

  // Columns run left to right and captures are in time order, so the search
  // for the first capture of a column carries on from the last one's.
  let cursor = 0;
  for (let column = 0; column < columns; column += 1) {
    const from = view.startS + (column * view.spanS) / columns;
    const toS = view.startS + ((column + 1) * view.spanS) / columns;
    while (cursor < islands.length && endOf(islands[cursor]) <= from) cursor += 1;

    let ended = 0;
    let seenHigh = 0;
    let seenLow = 0;
    let seen = false;
    for (let index = cursor; index < islands.length; index += 1) {
      const island = islands[index];
      if (island.startS >= toS) break;
      let first = Math.floor((from - island.startS) * island.rateHz);
      let to = Math.ceil((toS - island.startS) * island.rateHz);
      // Zoomed in past one sample per column, the column still shows the
      // sample it lands in rather than nothing.
      if (to <= first) to = first + 1;
      first = Math.max(0, first);
      to = Math.min(island.bytes.length, to);
      for (let sample = first; sample < to; sample += 1) {
        const byte = island.bytes[sample];
        seenHigh |= byte;
        seenLow |= ~byte;
        ended = byte;
        seen = true;
      }
    }
    high[column] = seenHigh & 0xff;
    last[column] = ended & 0xff;
    low[column] = seenLow & 0xff;
    present[column] = seen ? 1 : 0;
  }
  return { high, last, low, present };
}

/** What one channel's lane draws in each column of a view.

 * `levels` is 0 low, 1 high, 2 both within the column, and -1 before the first
 * capture. `ends` is the level a column finished at, carried on through the
 * gaps between captures: a driven pin holds its level while nothing is
 * watching it, so the lane joins up rather than breaking. `observed` says
 * which columns a capture actually covered, so the two can be told apart.
 */
export function laneLevels(
  masks: { high: Uint8Array; last: Uint8Array; low: Uint8Array; present: Uint8Array },
  channel: number,
  columns: number
): { ends: Int8Array; levels: Int8Array; observed: Uint8Array } {
  const ends = new Int8Array(columns).fill(-1);
  const levels = new Int8Array(columns).fill(-1);
  const observed = new Uint8Array(columns);

  let held = -1;
  for (let column = 0; column < columns; column += 1) {
    if (masks.present[column]) {
      const hasHigh = (masks.high[column] >> channel) & 1;
      const hasLow = (masks.low[column] >> channel) & 1;
      held = (masks.last[column] >> channel) & 1;
      levels[column] = hasHigh && hasLow ? 2 : hasHigh ? 1 : 0;
      observed[column] = 1;
    } else {
      levels[column] = held;
    }
    ends[column] = held;
  }
  return { ends, levels, observed };
}

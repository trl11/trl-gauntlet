/**
 * Turning a window of a DAQ recording into lines to draw.
 */

import type { DaqWindow } from "@api/types";
import type { Series } from "@components/DaqChart";

/**
 * One line per channel, joined across the stretches the recording was taken in.
 *
 * A raw window gives each channel one line. An envelope gives it two, the
 * lowest and the highest reading of each stretch, drawn in one colour, so a
 * spike is there however far out the view is. A reading the instrument did
 * not give is left out.
 */
export function seriesOf(window: DaqWindow, name: (label: string) => string): Series[] {
  const lines = new Map<string, Series>();
  for (const segment of window.segments) {
    const count = segment.channels.length;
    segment.channels.forEach((channel, column) => {
      const label = name(channel.label);
      const line = lines.get(label) ?? { instrument: window.instrument, name: label, points: [] };
      lines.set(label, line);
      for (const row of segment.points) {
        const low = row[1 + column];
        if (Number.isNaN(low)) continue;
        line.points.push({ t: row[0], v: low });
        if (segment.kind === "envelope") {
          (line.band ??= []).push({ t: row[0], v: row[1 + count + column] });
        }
      }
    });
  }
  return [...lines.values()];
}

/** The most points one window is asked for, which is what a graph's width can use. */
export const WINDOW_POINTS = 2000;

/** A view moved or resized, kept inside the span the recording covers. */
export function fitView(view: [number, number], span: [number, number]): [number, number] {
  const width = Math.min(view[1] - view[0], span[1] - span[0]);
  const start = Math.min(Math.max(view[0], span[0]), span[1] - width);
  return [start, start + width];
}

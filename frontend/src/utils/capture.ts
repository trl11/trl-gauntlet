/**
 * Reading a run's captured waveforms.
 *
 * A capture is CSV: a time column and one column per channel, under the metric
 * name the channel records as. Nothing here knows an instrument — the header
 * names the channels and the first column is seconds from the start of the
 * window.
 */

/** One capture: the channels it holds and every sample of them. */
export interface Capture {
  /** Channel names, in column order. */
  channels: string[];
  /** Seconds from the start of the window, one per row. */
  times: number[];
  /** Samples per channel, each array as long as `times`. */
  values: number[][];
}

/** One point of a drawn series: the time, then one value per channel. */
export interface CapturePoint {
  t: number;
  [channel: string]: number;
}

/**
 * Parse a capture CSV.
 *
 * A row that does not parse is dropped rather than failing the file: a run
 * killed mid-write leaves a half-written last line, and the rest of the
 * capture is still worth drawing.
 */
export function parseCapture(text: string): Capture {
  const lines = text.split("\n").filter((line) => line.trim() !== "");
  if (lines.length < 2) return { channels: [], times: [], values: [] };

  const header = lines[0].split(",").map((name) => name.trim());
  const channels = header.slice(1);
  const times: number[] = [];
  const values: number[][] = channels.map(() => []);

  for (const line of lines.slice(1)) {
    const cells = line.split(",");
    if (cells.length !== header.length) continue;
    const time = Number(cells[0]);
    if (!Number.isFinite(time)) continue;
    const row = cells.slice(1).map(Number);
    if (row.some((value) => !Number.isFinite(value))) continue;
    times.push(time);
    row.forEach((value, index) => values[index].push(value));
  }
  return { channels, times, values };
}

/**
 * Thin a capture down to something a chart can draw, keeping the extremes.
 *
 * A window of five thousand samples drawn into six hundred pixels has to lose
 * samples somewhere. Taking every nth would drop the peaks — the 95uV of
 * ripple on a 2.5mV rail lives entirely in them — so each bucket contributes
 * its lowest and its highest sample, in the order they occurred. The envelope
 * of the signal survives, which is what the eye reads a waveform by.
 *
 * A window already smaller than the budget is returned sample for sample,
 * which is what makes zooming in show the real thing rather than a smoothed
 * copy of it.
 */
export function decimate(
  capture: Capture,
  from: number,
  to: number,
  budget: number
): CapturePoint[] {
  const { channels, times, values } = capture;
  const start = Math.max(0, Math.min(from, times.length - 1));
  const end = Math.max(start, Math.min(to, times.length - 1));
  const width = end - start + 1;
  if (times.length === 0) return [];
  if (width <= budget) {
    return times.slice(start, end + 1).map((t, offset) => {
      const point: CapturePoint = { t };
      channels.forEach((name, index) => {
        point[name] = values[index][start + offset];
      });
      return point;
    });
  }

  const buckets = Math.max(1, Math.floor(budget / 2));
  const size = width / buckets;
  const points: CapturePoint[] = [];
  for (let bucket = 0; bucket < buckets; bucket += 1) {
    const low = start + Math.floor(bucket * size);
    const high = Math.min(end, start + Math.floor((bucket + 1) * size) - 1);
    if (high < low) continue;
    // The first channel decides where the bucket's extremes are, and every
    // channel is sampled at those two instants, so one point is one moment on
    // every trace rather than a different moment per trace.
    let lowest = low;
    let highest = low;
    const lead = values[0] ?? [];
    for (let index = low; index <= high; index += 1) {
      if (lead[index] < lead[lowest]) lowest = index;
      if (lead[index] > lead[highest]) highest = index;
    }
    for (const index of lowest <= highest ? [lowest, highest] : [highest, lowest]) {
      const point: CapturePoint = { t: times[index] };
      channels.forEach((name, channel) => {
        point[name] = values[channel][index];
      });
      points.push(point);
    }
  }
  return points;
}

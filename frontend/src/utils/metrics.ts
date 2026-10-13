/** One group of series names sharing a dotted prefix, e.g. "cpu" for `cpu.percent`. */
export interface SeriesGroup {
  group: string;
  names: string[];
}

/** How much of a series' own range is left as air above and below it. */
const HEADROOM = 0.1;

/**
 * The vertical window a chart draws a set of values in.
 *
 * A tenth of the range as air at each end, so the line is read against the
 * grid rather than against the edge of the panel, and a peak that touches the
 * extreme is still visibly a peak.
 *
 * A series that never moves has no range to take a tenth of, so the air comes
 * from the value itself — and from 1 where even that is zero, because a flat
 * line at zero still has to be drawn somewhere.
 */
export function paddedDomain(values: number[]): [number, number] | undefined {
  const real = values.filter((value) => Number.isFinite(value));
  if (real.length === 0) return undefined;
  const low = Math.min(...real);
  const high = Math.max(...real);
  const span = high - low;
  const air = span > 0 ? span * HEADROOM : Math.abs(high) * HEADROOM || 1;
  return [low - air, high + air];
}

/**
 * The vertical axis for the lines drawn: the range the instrument can read on
 * them when every line gives one, so the axis stays put whatever they read.
 * Otherwise everything they read, including a line's `min` and `max`, with a
 * tenth of the span as air either side. A live view passes the whole run's,
 * so the axis does not shrink to the last seconds and jump when they move.
 */
export function verticalDomain(
  drawn: { max?: number; min?: number; points: { v: number }[]; range?: [number, number] }[]
): [number, number] | undefined {
  const ranges = drawn.flatMap((line) => (line.range ? [line.range] : []));
  if (ranges.length > 0 && ranges.length === drawn.length) {
    return [
      Math.min(...ranges.map((range) => range[0])),
      Math.max(...ranges.map((range) => range[1])),
    ];
  }
  return paddedDomain(
    drawn.flatMap((line) => [
      ...line.points.map((point) => point.v),
      ...(line.min != null ? [line.min] : []),
      ...(line.max != null ? [line.max] : []),
    ])
  );
}

/** Splits `a1 < a2 < a10` the way a person reads it, not the way `localeCompare` does. */
export function naturalCompare(a: string, b: string): number {
  const chunk = (value: string) => value.match(/\d+|\D+/g) ?? [value];
  const left = chunk(a);
  const right = chunk(b);
  const length = Math.max(left.length, right.length);
  for (let i = 0; i < length; i++) {
    const x = left[i] ?? "";
    const y = right[i] ?? "";
    if (x === y) continue;
    if (x === "") return -1;
    if (y === "") return 1;
    const bothNumbers = /^\d+$/.test(x) && /^\d+$/.test(y);
    if (bothNumbers && Number(x) !== Number(y)) return Number(x) - Number(y);
    return x < y ? -1 : 1;
  }
  return 0;
}

/**
 * Host-stat series with no dot in their name, hand-placed into the group they
 * read as part of rather than forming a singleton group of one.
 */
const GROUP_ALIASES: Record<string, string> = {
  context_switches_per_s: "cpu",
  cpu_count: "cpu",
  thermal_max_c: "thermal",
  uptime_s: "cpu",
  window_s: "other",
};

/**
 * Buckets series names by the part before their first dot, so `cpu.per_core.cpu0`
 * and `cpu.percent` land in the same "cpu" group. Names with no dot form their own
 * group, except the few in {@link GROUP_ALIASES} that join an existing one. Groups
 * are ordered alphabetically; names inside a group sort naturally, so `cpu9` comes
 * before `cpu10`.
 */
export function groupSeriesNames(names: string[]): SeriesGroup[] {
  const groups = new Map<string, string[]>();
  for (const name of names) {
    const dot = name.indexOf(".");
    const group = dot === -1 ? (GROUP_ALIASES[name] ?? name) : name.slice(0, dot);
    const list = groups.get(group);
    if (list) list.push(name);
    else groups.set(group, [name]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([group, list]) => ({ group, names: list.sort(naturalCompare) }));
}

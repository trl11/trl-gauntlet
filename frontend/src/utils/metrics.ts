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
    const xNum = Number(x);
    const yNum = Number(y);
    if (!Number.isNaN(xNum) && !Number.isNaN(yNum)) return xNum - yNum;
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

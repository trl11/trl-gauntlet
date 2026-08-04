/** One group of series names sharing a dotted prefix, e.g. "cpu" for `cpu.percent`. */
export interface SeriesGroup {
  group: string;
  names: string[];
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
 * Buckets series names by the part before their first dot, so `cpu.per_core.cpu0`
 * and `cpu.percent` land in the same "cpu" group. Names with no dot form their own
 * group. Groups are ordered alphabetically; names inside a group sort naturally,
 * so `cpu9` comes before `cpu10`.
 */
export function groupSeriesNames(names: string[]): SeriesGroup[] {
  const groups = new Map<string, string[]>();
  for (const name of names) {
    const dot = name.indexOf(".");
    const group = dot === -1 ? name : name.slice(0, dot);
    const list = groups.get(group);
    if (list) list.push(name);
    else groups.set(group, [name]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([group, list]) => ({ group, names: list.sort(naturalCompare) }));
}

/** Presentation helpers shared by every page. */

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/** Divisor to reach the next coarser unit, paired with that unit's name. */
const RELATIVE_STEPS: Array<[number, Intl.RelativeTimeFormatUnit]> = [
  [60, "minute"],
  [60, "hour"],
  [24, "day"],
  [7, "week"],
  [4.348, "month"],
  [12, "year"],
];

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/**
 * Parse an API timestamp into a `Date`.
 *
 * Accepts an ISO-8601 string, epoch seconds, or epoch milliseconds. Values
 * below 1e11 are read as seconds, which covers every plausible run time.
 * Returns `null` when the value cannot be parsed.
 */
export function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value == null || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return new Date(Math.abs(value) < 1e11 ? value * 1000 : value);
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * Format a duration in seconds as `1d 2h 3m 4s`.
 *
 * Leading and trailing zero units are dropped, mid zeros are kept so
 * `1h 0m 1s` stays unambiguous, and sub-second values render as milliseconds.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "-";
  if (seconds === 0) return "0s";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;

  const total = Math.floor(seconds);
  const parts: Array<[number, string]> = [
    [Math.floor(total / 86400), "d"],
    [Math.floor((total % 86400) / 3600), "h"],
    [Math.floor((total % 3600) / 60), "m"],
    [total % 60, "s"],
  ];
  const start = parts.findIndex(([value]) => value > 0);
  if (start === -1) return "0s";
  let end = parts.length - 1;
  while (end > start && parts[end][0] === 0) end -= 1;
  return parts
    .slice(start, end + 1)
    .map(([value, unit]) => `${value}${unit}`)
    .join(" ");
}

/** Format a byte count with a binary-scaled unit, as `1.4 MB`. */
export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes == null || !Number.isFinite(bytes)) return "-";
  const sign = bytes < 0 ? "-" : "";
  let value = Math.abs(bytes);
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const precision = unit === 0 ? 0 : digits;
  return `${sign}${value.toFixed(precision)} ${BYTE_UNITS[unit]}`;
}

/** Render a timestamp in the viewer's locale, 24-hour, to the second. */
export function formatTimestamp(
  value: string | number | Date | null | undefined,
  options?: Intl.DateTimeFormatOptions
): string {
  const date = toDate(value);
  if (date === null) return "-";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    ...options,
  });
}

/** Render how long ago a timestamp was, as `5 minutes ago`. */
export function formatRelativeTime(
  value: string | number | Date | null | undefined,
  now: Date = new Date()
): string {
  const date = toDate(value);
  if (date === null) return "-";

  let delta = (date.getTime() - now.getTime()) / 1000;
  if (Math.abs(delta) < 5) return "just now";

  let unit: Intl.RelativeTimeFormatUnit = "second";
  for (const [size, next] of RELATIVE_STEPS) {
    if (Math.abs(delta) < size) break;
    delta /= size;
    unit = next;
  }
  return relative.format(Math.round(delta), unit);
}

/**
 * Render a number with grouping separators.
 *
 * `digits` fixes the fraction length; omitted, up to three decimals are kept
 * and trailing zeros dropped.
 */
export function formatNumber(value: number | null | undefined, digits?: number): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits ?? 0,
    maximumFractionDigits: digits ?? 3,
  });
}

/** Render a 0-100 percentage as `42.5%`. */
export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value.toFixed(digits)}%`;
}

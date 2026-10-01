/**
 * The limits an operator sets on a streaming instrument's channels.
 *
 * An entry holds what was typed. A channel starts at the most and the least
 * the instrument can read, and a limit left there watches nothing, so a
 * channel is watched only where a limit has been moved inside that range.
 */

import type { StartUpsetInstrument, StreamChannel, UpsetLimits } from "@api/types";

/** One channel's low and high entries, as typed. */
export interface LimitEntry {
  high: string;
  low: string;
}

/** Everything typed for one instrument. */
export interface DaqSettings {
  /** Seconds kept after a crossing. */
  after: string;
  /** Seconds kept before a crossing. */
  before: string;
  /** Whether each channel is in the scan list, by channel key. */
  enabled: Record<string, boolean>;
  /** Entries by channel key. */
  entries: Record<string, LimitEntry>;
}

/** Seconds kept either side of a crossing until an operator says otherwise. */
export const DEFAULT_WINDOW_S = 2;

function show(value: number | undefined): string {
  return value === undefined ? "" : String(value);
}

/**
 * Settings to start from: every channel at the range it can read, or at the
 * limits the run already holds where it holds any.
 */
export function defaultSettings(
  channels: StreamChannel[],
  held?: { channels: Record<string, UpsetLimits>; post_s?: number; pre_s?: number }
): DaqSettings {
  return {
    after: String(held?.post_s ?? DEFAULT_WINDOW_S),
    before: String(held?.pre_s ?? DEFAULT_WINDOW_S),
    enabled: Object.fromEntries(channels.map((channel) => [channel.key, channel.enabled ?? true])),
    entries: Object.fromEntries(
      channels.map((channel) => {
        const limits = held?.channels[channel.key];
        return [
          channel.key,
          {
            high: limits?.high != null ? String(limits.high) : show(channel.max),
            low: limits?.low != null ? String(limits.low) : show(channel.min),
          },
        ];
      })
    ),
  };
}

/**
 * Settings for picking which channels a run scans: none is picked to begin
 * with, and while none is the instrument is left as it is.
 */
export function pickableSettings(channels: StreamChannel[]): DaqSettings {
  const settings = defaultSettings(channels);
  return {
    ...settings,
    enabled: Object.fromEntries(channels.map((channel) => [channel.key, false])),
  };
}

/** A limit as typed: null when it is empty or still at the end of the range, NaN when it is not a number. */
function limitOf(text: string, bound: number | undefined): number | null {
  if (text.trim() === "") return null;
  const value = Number(text);
  return value === bound ? null : value;
}

/** The limits typed, each null where it watches nothing. Throws on a limit that is not a number. */
export function limitsOf(
  settings: DaqSettings,
  channels: StreamChannel[]
): Record<string, UpsetLimits> {
  const limits: Record<string, UpsetLimits> = {};
  for (const channel of channels) {
    const entry = settings.entries[channel.key];
    if (!entry || settings.enabled[channel.key] === false) continue;
    const low = limitOf(entry.low, channel.min);
    const high = limitOf(entry.high, channel.max);
    if (Number.isNaN(low) || Number.isNaN(high)) {
      throw new Error(`${channel.label}: a limit must be a number`);
    }
    limits[channel.key] = { high, low };
  }
  return limits;
}

/**
 * What a run is started with for one instrument, or null while no channel is
 * picked and the instrument is left as it is.
 */
export function startInstrument(
  settings: DaqSettings,
  channels: StreamChannel[]
): StartUpsetInstrument | null {
  const limits = limitsOf(settings, channels);
  const watched = Object.fromEntries(
    Object.entries(limits).filter(([, limit]) => limit.high !== null || limit.low !== null)
  );
  if (!channels.some((channel) => settings.enabled[channel.key])) return null;
  return {
    channels: watched,
    enabled: settings.enabled,
    post_s: Number(settings.after),
    pre_s: Number(settings.before),
  };
}

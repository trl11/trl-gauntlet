import { Tooltip } from "@trl11/components/ui";
import clsx from "clsx";
import { useMemo } from "react";

import { formatDuration } from "../utils/format";

import "./PhaseTimeline.scss";

/** One completed phase inside an iteration. */
export interface PhaseRow {
  detail: Record<string, unknown>;
  elapsed_s: number;
  iteration: number | null;
  phase: string;
  success: boolean;
}

/** Props for {@link PhaseTimeline}. */
export interface PhaseTimelineProps {
  /** Phases in the order they were reported. */
  phases: PhaseRow[];
}

interface Track {
  iteration: number | null;
  phases: PhaseRow[];
  total: number;
}

function toTracks(phases: PhaseRow[]): Track[] {
  const tracks: Track[] = [];
  for (const phase of phases) {
    const last = tracks[tracks.length - 1];
    if (last && last.iteration === phase.iteration) {
      last.phases.push(phase);
      last.total += Math.max(phase.elapsed_s, 0);
      continue;
    }
    tracks.push({
      iteration: phase.iteration,
      phases: [phase],
      total: Math.max(phase.elapsed_s, 0),
    });
  }
  return tracks;
}

function describe(phase: PhaseRow): string {
  const detail = Object.entries(phase.detail ?? {})
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" ");
  const head = `${phase.phase} - ${formatDuration(phase.elapsed_s)} - ${
    phase.success ? "ok" : "failed"
  }`;
  return detail ? `${head} - ${detail}` : head;
}

/** Every iteration's phases as one proportional bar per iteration. */
export const PhaseTimeline: React.FC<PhaseTimelineProps> = ({ phases }) => {
  const tracks = useMemo(() => toTracks(phases), [phases]);

  if (tracks.length === 0) {
    return <p className="phase-timeline__empty">No phases have been reported for this run.</p>;
  }

  return (
    <section className="phase-timeline" aria-label="Phases">
      {tracks.map((track, index) => (
        <div className="phase-timeline__track" key={`${track.iteration}-${index}`}>
          <span className="phase-timeline__label">
            {track.iteration == null ? "run" : `#${track.iteration}`}
          </span>
          <div className="phase-timeline__bar">
            {track.phases.map((phase, position) => (
              <Tooltip
                content={describe(phase)}
                key={`${phase.phase}-${position}`}
                style={{
                  flexGrow: track.total > 0 ? Math.max(phase.elapsed_s, 0) / track.total : 1,
                }}
              >
                <span
                  aria-label={describe(phase)}
                  className={clsx(
                    "phase-timeline__segment",
                    phase.success
                      ? "phase-timeline__segment--ok"
                      : "phase-timeline__segment--failed"
                  )}
                  tabIndex={0}
                >
                  <span className="phase-timeline__name">{phase.phase}</span>
                </span>
              </Tooltip>
            ))}
          </div>
          <span className="phase-timeline__total">{formatDuration(track.total)}</span>
        </div>
      ))}
    </section>
  );
};

export default PhaseTimeline;

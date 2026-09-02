import { faDownload, faMagnifyingGlassMinus } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useQuery } from "@tanstack/react-query";
import { Button, Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { artifactUrl, getArtifactText } from "@api/client";
import EmptyState from "@components/EmptyState";
import {
  axisTicks,
  captureMarks,
  extentOf,
  formatSeconds,
  laneLevels,
  parseCaptures,
  timelineMasks,
  type TimeView,
} from "../utils/waveform";

import "./TraceTimeline.scss";

/** Height of one channel's lane, in CSS pixels. */
const LANE_HEIGHT = 34;

/** Gap between a lane's high level and the edge of its lane. */
const LANE_PADDING = 7;

/** How often the file is re-read while the run is still appending to it. */
const LIVE_POLL_MS = 5000;

/** Divisions across the plot, so a time step names a span. */
const DIVISIONS = 10;

/** What one wheel notch multiplies or divides the span by. */
const ZOOM_STEP = 1.25;

/** How close two captures may be named before the second is left unlabelled. */
const MARK_GAP = 0.06;

/** The time steps offered, as seconds per division. */
const STEPS = [
  1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5, 1e-4, 2e-4, 5e-4, 1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2, 0.1,
  0.2, 0.5, 1, 2, 5, 10, 20, 30, 60,
];

/** Props for {@link TraceTimeline}. */
export interface TraceTimelineProps {
  /** Poll the file while the run is in flight. */
  live?: boolean;
  /** Path of the captures file inside the run directory. */
  path: string;
  /** Run the captures belong to, used to build the URL. */
  runId: string;
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/**
 * How a label sits against the position it names.
 *
 * Centred over it, except at the two ends, where a centred label would hang
 * off the side of the plot.
 */
function anchorFor(fraction: number): string {
  if (fraction <= 0) return "translateX(0)";
  if (fraction >= 1) return "translateX(-100%)";
  return "translateX(-50%)";
}

/** The offered step closest to what a span works out at per division. */
function stepFor(spanS: number): number {
  const wanted = spanS / DIVISIONS;
  return STEPS.reduce((best, step) =>
    Math.abs(step - wanted) < Math.abs(best - wanted) ? step : best
  );
}

/**
 * A time the operator types, held as text until they are done with it.
 *
 * Reformatting on every keystroke would rewrite what is being typed after each
 * character, so the field keeps its own draft and hands the number over on
 * Enter or when it loses focus.
 */
const RangeInput: React.FC<{
  label: string;
  onCommit: (value: number) => void;
  value: string;
}> = ({ label, onCommit, value }) => {
  const [draft, setDraft] = useState<string | null>(null);

  const commit = () => {
    if (draft === null) return;
    const typed = Number(draft);
    if (Number.isFinite(typed)) onCommit(typed);
    setDraft(null);
  };

  return (
    <input
      aria-label={label}
      className="trace-timeline__input"
      inputMode="decimal"
      value={draft ?? value}
      onBlur={commit}
      onChange={(event) => setDraft(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") commit();
      }}
    />
  );
};

/**
 * Every capture of a run on one timeline, drawn where each happened.
 *
 * A run samples a window at a time, so the captures are islands with the rest
 * of the run between them. The lanes are drawn only where there is a capture:
 * the gaps are left empty rather than held at a level, because nothing was
 * watching the line there.
 *
 * The file is whatever the suite named in `metrics.traces`; nothing here knows
 * which suite wrote it or what instrument captured it. Its shape is the one
 * `docs/contract.md` gives for a run's captures.
 */
export const TraceTimeline: React.FC<TraceTimelineProps> = ({ live = false, path, runId }) => {
  const [view, setView] = useState<TimeView | null>(null);
  const [panning, setPanning] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragRef = useRef<{ startS: number; x: number } | null>(null);

  const file = useQuery({
    queryKey: ["captures", runId, path],
    queryFn: () => getArtifactText(runId, path),
    refetchInterval: live ? LIVE_POLL_MS : false,
  });

  const captures = useMemo(
    () => (file.data === undefined ? null : parseCaptures(file.data)),
    [file.data]
  );
  const islands = captures?.islands ?? [];
  const channels = captures?.channels ?? [];
  const whole = useMemo(() => extentOf(islands), [islands]);

  // A run that is still appending grows its extent, so the view is only set
  // from it while there is none, which leaves a zoomed-in operator where they
  // were.
  useEffect(() => {
    setView((current) => (current === null && whole.spanS > 0 ? whole : current));
  }, [whole]);

  const move = useCallback(
    (next: TimeView) => {
      const spanS = clamp(next.spanS, 1e-9, whole.spanS);
      const startS = clamp(next.startS, whole.startS, whole.startS + whole.spanS - spanS);
      setView({ spanS, startS });
    },
    [whole]
  );

  const zoomAt = useCallback(
    (fraction: number, factor: number) => {
      setView((current) => {
        if (current === null) return current;
        const spanS = clamp(current.spanS * factor, 1e-9, whole.spanS);
        const anchor = current.startS + fraction * current.spanS;
        return {
          spanS,
          startS: clamp(
            anchor - fraction * spanS,
            whole.startS,
            whole.startS + whole.spanS - spanS
          ),
        };
      });
    },
    [whole]
  );

  // React attaches wheel listeners passively, which cannot stop the page
  // scrolling behind the plot, so this one is attached by hand.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const box = canvas.getBoundingClientRect();
      const fraction = box.width > 0 ? (event.clientX - box.left) / box.width : 0.5;
      zoomAt(clamp(fraction, 0, 1), event.deltaY < 0 ? 1 / ZOOM_STEP : ZOOM_STEP);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null || view === null || channels.length === 0) return;

    const style = getComputedStyle(canvas);
    const traceColour = style.getPropertyValue("--waveform-trace").trim();
    const heldColour = style.getPropertyValue("--waveform-held").trim();
    const guide = style.getPropertyValue("--waveform-guide").trim();

    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.floor(canvas.clientWidth));
    const height = channels.length * LANE_HEIGHT;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    canvas.style.height = `${height}px`;

    const context = canvas.getContext("2d");
    if (context === null) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const masks = timelineMasks(islands, view, width);

    for (let channel = 0; channel < channels.length; channel += 1) {
      const top = channel * LANE_HEIGHT + LANE_PADDING;
      const bottom = (channel + 1) * LANE_HEIGHT - LANE_PADDING;
      const yOf = (level: number) => (level === 1 ? top : bottom);
      const { ends, levels, observed } = laneLevels(masks, channel, width);

      context.strokeStyle = guide;
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(0, channel * LANE_HEIGHT + 0.5);
      context.lineTo(width, channel * LANE_HEIGHT + 0.5);
      context.stroke();

      // Twice over: once for the columns a capture covered and once for the
      // stretches between them, so an inferred level is not drawn as though
      // it were measured. A transition belongs to the run it leads into.
      for (const wanted of [0, 1]) {
        context.strokeStyle = wanted ? traceColour : heldColour;
        context.lineWidth = wanted ? 1.5 : 1;
        context.beginPath();
        let previous: number | null = null;
        for (let column = 0; column < width; column += 1) {
          const level = levels[column];
          if (level < 0) {
            previous = null;
            continue;
          }
          const mine = observed[column] === wanted;
          if (level === 2) {
            if (mine) {
              context.moveTo(column + 0.5, bottom);
              context.lineTo(column + 0.5, top);
            }
            previous = yOf(ends[column]);
            continue;
          }
          const y = yOf(level);
          if (mine) {
            if (previous !== null && previous !== y) {
              context.moveTo(column + 0.5, previous);
              context.lineTo(column + 0.5, y);
            }
            context.moveTo(column, y);
            context.lineTo(column + 1, y);
          }
          previous = y;
        }
        context.stroke();
      }
    }
  }, [channels, islands, view]);

  if (file.isPending) return <Spinner />;

  if (file.isError) {
    return (
      <EmptyState
        title="Traces unavailable"
        message={file.error instanceof Error ? file.error.message : path}
      />
    );
  }

  if (islands.length === 0) {
    return <EmptyState title="No traces" message="This run has not recorded any captures yet." />;
  }

  const step = view === null ? STEPS[0] : stepFor(view.spanS);
  const fitted = view !== null && view.spanS >= whole.spanS;
  const ticks = view === null ? [] : axisTicks(view, DIVISIONS);
  const marks = view === null ? [] : captureMarks(islands, view, MARK_GAP);

  return (
    <section className="trace-timeline" aria-label="Traces">
      <div className="trace-timeline__bar">
        <div className="trace-timeline__group">
          <label className="trace-timeline__field">
            Step
            <select
              aria-label="Time per division"
              className="trace-timeline__select"
              value={step}
              onChange={(event) => {
                if (view === null) return;
                move({ ...view, spanS: Number(event.target.value) * DIVISIONS });
              }}
            >
              {STEPS.map((option) => (
                <option key={option} value={option}>
                  {formatSeconds(option)}/div
                </option>
              ))}
            </select>
          </label>
          <label className="trace-timeline__field">
            From
            <RangeInput
              label="Range start, in seconds into the run"
              value={view === null ? "" : view.startS.toPrecision(6)}
              onCommit={(startS) => {
                // The step sets how wide the window is, so naming an edge
                // slides that window rather than resizing it.
                if (view !== null) move({ ...view, startS });
              }}
            />
          </label>
          <label className="trace-timeline__field">
            To
            <RangeInput
              label="Range end, in seconds into the run"
              value={view === null ? "" : (view.startS + view.spanS).toPrecision(6)}
              onCommit={(end) => {
                if (view !== null) move({ ...view, startS: end - view.spanS });
              }}
            />
          </label>
        </div>
        <div className="trace-timeline__group">
          <span className="trace-timeline__reading">
            {`${islands.length.toLocaleString()} captures over ${formatSeconds(whole.spanS)}`}
          </span>
          <Button
            aria-label="Fit the whole run"
            disabled={fitted}
            size="small"
            onClick={() => setView(whole)}
          >
            <FontAwesomeIcon icon={faMagnifyingGlassMinus} />
          </Button>
          <a
            aria-label="Download the captures"
            className="trace-timeline__reading"
            download
            href={artifactUrl(runId, path)}
          >
            <FontAwesomeIcon icon={faDownload} />
          </a>
        </div>
      </div>

      <div className="trace-timeline__plot">
        <span className="trace-timeline__gutter">Iteration</span>
        <div className="trace-timeline__marks">
          {marks.map((mark) => (
            <span
              className="trace-timeline__mark"
              key={mark.iteration}
              style={{ left: `${mark.fraction * 100}%` }}
            >
              <span
                className="trace-timeline__mark-label"
                style={{ transform: anchorFor(mark.fraction) }}
              >
                {mark.iteration}
              </span>
            </span>
          ))}
        </div>
        <div className="trace-timeline__labels">
          {channels.map((label, channel) => (
            <span
              className="trace-timeline__label"
              key={`${label}-${channel}`}
              style={{ height: LANE_HEIGHT }}
            >
              {label || `CH ${channel + 1}`}
            </span>
          ))}
        </div>
        <canvas
          aria-label={`${islands.length} captures, ${channels.length} channels`}
          className={clsx("trace-timeline__canvas", panning && "trace-timeline__canvas--panning")}
          ref={canvasRef}
          role="img"
          onPointerDown={(event) => {
            if (view === null) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            dragRef.current = { startS: view.startS, x: event.clientX };
            setPanning(true);
          }}
          onPointerMove={(event) => {
            const drag = dragRef.current;
            if (drag === null || view === null) return;
            const width = event.currentTarget.clientWidth || 1;
            const moved = ((event.clientX - drag.x) / width) * view.spanS;
            move({ ...view, startS: drag.startS - moved });
          }}
          onPointerUp={(event) => {
            event.currentTarget.releasePointerCapture(event.pointerId);
            dragRef.current = null;
            setPanning(false);
          }}
        />
        <span className="trace-timeline__gutter">Time</span>
        <div className="trace-timeline__axis">
          {ticks.map((tick) => (
            <span
              className="trace-timeline__tick"
              key={tick.fraction}
              style={{ left: `${tick.fraction * 100}%` }}
            >
              <span
                className="trace-timeline__tick-label"
                style={{ transform: anchorFor(tick.fraction) }}
              >
                {tick.label}
              </span>
            </span>
          ))}
        </div>
      </div>

      <p className="trace-timeline__hint">
        The step sets how much time is in view; From and To move that window over the run. Scroll to
        zoom, drag to pan. A bright line is a capture; a dim one joins two captures at the level the
        last one ended on, which was not measured. The number above a capture is the iteration that
        recorded it, left off where two would land on top of each other.
      </p>
    </section>
  );
};

export default TraceTimeline;

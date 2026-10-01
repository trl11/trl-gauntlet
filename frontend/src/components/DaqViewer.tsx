import { useQueries, useQuery } from "@tanstack/react-query";
import { Button, Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useMemo, useRef, useState } from "react";
import {
  Brush,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getArtifactText, getUpsetTrace } from "@api/client";
import type { UpsetEntry, UpsetScan, UpsetSummary, UpsetTrace } from "@api/types";
import DaqRecording from "@components/DaqRecording";
import EmptyState from "@components/EmptyState";
import {
  ChannelToggles,
  COLOR_COUNT,
  LayoutToggle,
  SeriesChart,
  type Series,
} from "@components/DaqChart";
import useHidden from "@hooks/useHidden";
import useLayout from "@hooks/useLayout";
import { decimate, parseCapture, windowDomain, type Capture } from "../utils/capture";
import { formatNumber, formatTimestamp } from "../utils/format";
import { paddedDomain } from "../utils/metrics";

import "./DaqViewer.scss";

/** Milliseconds between polls of a streaming instrument's scans. */
const TRACE_POLL_MS = 200;

/** Seconds of the live trace kept and drawn. */
const TRACE_WINDOW_S = 30;

/** The live trace's time axis: the last seconds, ending now. */
const LIVE_DOMAIN: [number, number] = [-TRACE_WINDOW_S, 0];

/** Height of a graph that has a channel to itself. */
const SEPARATE_HEIGHT = 150;

/** Scans a second the live trace is drawn at. The instrument is recorded at its own rate. */
const LIVE_DISPLAY_HZ = 50;

/** Points drawn at once in an event's window, which is the recording itself and is shown in detail. */
const EVENT_BUDGET = 8000;

/** Props for {@link DaqViewer}. */
export interface DaqViewerProps {
  /** True while the run is in flight, which is when limits can be set. */
  live: boolean;
  /** True for a moment after an event arrives, to draw the eye. */
  flashing: boolean;
  /** Opens this view in a window of its own, from a key beside the layout choice. Offered only when given. */
  onPopOut?: () => void;
  /** Run being watched. */
  runId: string;
  /** What the run has recorded, and which instruments it is watching. */
  summary: UpsetSummary;
}

/** The scans kept from the live stream, and where to resume polling. */
interface TraceBuffer {
  channels: UpsetTrace["channels"];
  cursor: number;
  rate_hz: number;
  scans: UpsetScan[];
}

/**
 * One event's window: every channel over the seconds around the crossing, with
 * the crossing marked at zero. A window brushed down shows the samples
 * themselves, so zooming in shows what the instrument read.
 */
const CaptureChart: React.FC<{ capture: Capture; event: UpsetEntry }> = ({ capture, event }) => {
  const [hidden, toggle] = useHidden();
  const [window, setWindow] = useState<[number, number] | null>(null);
  const depth = capture.times.length;
  const [from, to] = window ?? [0, Math.max(0, depth - 1)];
  const points = useMemo(() => decimate(capture, from, to, EVENT_BUDGET), [capture, from, to]);
  const drawn = capture.channels.filter((name) => !hidden.includes(name));
  const crossed = capture.channels.indexOf(event.label);

  return (
    <div className="daq-viewer__capture">
      <div className="daq-viewer__controls">
        <ChannelToggles hidden={hidden} names={capture.channels} onToggle={toggle} />
        <Button disabled={window === null} onClick={() => setWindow(null)} size="small">
          Reset zoom
        </Button>
      </div>
      <p className="daq-viewer__note">
        {`${formatNumber(depth, 0)} scans around the crossing`}
        {to - from + 1 < depth ? `, showing ${formatNumber(to - from + 1, 0)} of them` : ""}. Drag
        the bar beneath the chart to zoom and scroll.
      </p>
      <div className="daq-viewer__chart">
        <ResponsiveContainer width="100%" height={380}>
          <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              allowDataOverflow
              dataKey="t"
              domain={windowDomain(event, window, capture.times)}
              tickFormatter={(value: number) => `${formatNumber(value, 3)}s`}
              type="number"
            />
            <YAxis
              allowDataOverflow
              domain={paddedDomain(points.flatMap((point) => drawn.map((name) => point[name])))}
              tickFormatter={(value: number) => formatNumber(value, 4)}
              width={72}
            />
            <ChartTooltip
              formatter={(value: number | string) => formatNumber(Number(value), 6)}
              labelFormatter={(value: number | string) => `${formatNumber(Number(value), 4)} s`}
            />
            <ReferenceLine className="daq-viewer__crossing" x={0} />
            <ReferenceLine className="daq-viewer__limit" y={event.limit} />
            {drawn.map((name) => {
              const index = capture.channels.indexOf(name);
              return (
                <Line
                  className={`daq-chart__line daq-chart__line--c${index % COLOR_COUNT}`}
                  dataKey={name}
                  dot={false}
                  isAnimationActive={false}
                  key={name}
                  stroke="currentColor"
                  strokeWidth={index === crossed ? 1.8 : 1}
                  type="linear"
                />
              );
            })}
            <Brush
              dataKey="t"
              height={22}
              onChange={(next: { startIndex?: number; endIndex?: number }) => {
                const first = points[next.startIndex ?? 0];
                const last = points[next.endIndex ?? points.length - 1];
                if (!first || !last) return;
                // The brush indexes what is drawn, which is an envelope of the
                // samples, so its window is turned back into sample numbers
                // before the next draw thins them.
                const startAt = capture.times.findIndex((t) => t >= first.t);
                const endAt = capture.times.findIndex((t) => t >= last.t);
                setWindow([startAt < 0 ? 0 : startAt, endAt < 0 ? depth - 1 : endAt]);
              }}
              tickFormatter={(value: number) => `${formatNumber(value, 2)}s`}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

/** One event's captured window, read from the run directory. */
const EventCapture: React.FC<{ event: UpsetEntry; runId: string }> = ({ event, runId }) => {
  const file = useQuery({
    queryKey: ["upset-capture", runId, event.file],
    queryFn: () => getArtifactText(runId, event.file),
  });
  const capture = useMemo(() => parseCapture(file.data ?? ""), [file.data]);
  if (file.isPending) return <Spinner className="daq-viewer__spinner" />;
  if (file.isError || capture.times.length === 0) {
    return <EmptyState title="No samples" message={`Nothing could be read from ${event.file}.`} />;
  }
  return <CaptureChart capture={capture} event={event} key={event.file} />;
};

/** The events in a run, newest first, and the window of the one selected. */
const EventList: React.FC<{ events: UpsetEntry[]; runId: string; stopAfter: number }> = ({
  events,
  runId,
  stopAfter,
}) => {
  const [chosen, setChosen] = useState<number | null>(null);
  const newest = [...events].sort((a, b) => b.index - a.index);
  // The latest is shown until one is picked, so a finished run opens on data.
  const selected = newest.find((event) => event.index === chosen) ?? newest[0] ?? null;

  if (events.length === 0) {
    return <p className="daq-viewer__note">No DAQ events yet.</p>;
  }
  return (
    <div className="daq-viewer__events">
      <p className="daq-viewer__note">
        {stopAfter > 0
          ? `${events.length} of ${stopAfter} events before the run stops.`
          : `${events.length} ${events.length === 1 ? "event" : "events"}.`}
      </p>
      <table className="daq-viewer__table">
        <thead>
          <tr>
            <th>#</th>
            <th>Time</th>
            <th>Run time</th>
            <th>Channel</th>
            <th>Limit</th>
            <th>Reading</th>
          </tr>
        </thead>
        <tbody>
          {newest.map((event) => (
            <tr
              aria-selected={selected?.index === event.index}
              className={clsx(selected?.index === event.index && "is-selected")}
              key={event.index}
              onClick={() => setChosen(event.index)}
            >
              <td>
                <button className="daq-viewer__open" type="button">
                  {event.index}
                </button>
              </td>
              <td>{formatTimestamp(event.at)}</td>
              <td>{formatNumber(event.elapsed_s, 2)} s</td>
              <td>
                {event.label}
                {event.truncated && " (cut short)"}
              </td>
              <td>
                {event.direction} {formatNumber(event.limit, 4)} {event.unit}
              </td>
              <td>
                {formatNumber(event.value, 4)} {event.unit}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected && <EventCapture event={selected} runId={runId} />}
    </div>
  );
};

/**
 * The live traces of every instrument the run watches, in one view.
 *
 * Each is on a graph of its own or all are stacked on one, and the same
 * buttons leave a channel out of either. Recording is at each instrument's own
 * rate; what is drawn is thinned to a rate a page can follow.
 */
const LiveTraces: React.FC<{
  flashing: boolean;
  instruments: string[];
  onPopOut?: () => void;
  runId: string;
}> = ({ flashing, instruments, onPopOut, runId }) => {
  const [hidden, toggle] = useHidden();
  // What the last polls added up to, so each asks only for the scans it has
  // not seen and a stream is never read twice.
  const buffers = useRef<Record<string, TraceBuffer>>({});

  const traces = useQueries({
    queries: instruments.map((instrument) => ({
      queryKey: ["upset-trace", runId, instrument],
      queryFn: async () => {
        const held = buffers.current[instrument] ?? {
          channels: [],
          cursor: 1,
          rate_hz: 0,
          scans: [],
        };
        const fetched = await getUpsetTrace(runId, instrument, held.cursor, {
          displayHz: LIVE_DISPLAY_HZ,
          // The first request starts from what is worth drawing, not from the oldest scan held.
          tailS: held.cursor <= 1 ? TRACE_WINDOW_S : undefined,
        });
        const layoutOf = (channels: UpsetTrace["channels"]) =>
          channels.map((channel) => channel.key).join();
        const kept = layoutOf(held.channels) === layoutOf(fetched.channels);
        const scans = [...(kept ? held.scans : []), ...fetched.scans];
        const newest = scans.length > 0 ? scans[scans.length - 1][1] : 0;
        const next: TraceBuffer = {
          channels: fetched.channels,
          cursor: fetched.next_seq,
          rate_hz: fetched.rate_hz,
          scans: scans.filter((scan) => scan[1] >= newest - TRACE_WINDOW_S),
        };
        buffers.current[instrument] = next;
        return next;
      },
      // A refusal, such as a run that has just ended, is not asked again.
      refetchInterval: (query: { state: { status: string } }) =>
        query.state.status === "error" ? false : TRACE_POLL_MS,
      retry: false,
    })),
  });

  const [layout, setLayout] = useLayout(
    traces.reduce((total, trace) => total + (trace.data?.channels.length ?? 0), 0)
  );

  if (traces.every((trace) => trace.isPending)) return <Spinner className="daq-viewer__spinner" />;

  // Every line is timed from the newest scan of any instrument, so stacked
  // lines meet at "now".
  const now = Math.max(
    0,
    ...traces.flatMap((trace) => (trace.data?.scans.length ? [trace.data.scans.at(-1)![1]] : []))
  );
  const several = instruments.length > 1;
  const series: Series[] = instruments.flatMap((instrument, index) => {
    const data = traces[index].data;
    if (!data) return [];
    return data.channels.map((channel, column) => ({
      instrument,
      name: several ? `${instrument} · ${channel.label}` : channel.label,
      points: data.scans
        .filter((scan) => scan[2][column] != null)
        .map((scan) => ({ t: scan[1] - now, v: scan[2][column] as number })),
    }));
  });
  const names = series.map((line) => line.name);
  const flash = flashing && "daq-viewer__trace--flash";
  const rates = instruments
    .flatMap((instrument, index) => {
      const data = traces[index].data;
      return data
        ? [
            `${several ? `${instrument}: ` : ""}${formatNumber(data.rate_hz, 2)} scans per second recorded${
              data.rate_hz > LIVE_DISPLAY_HZ ? `, drawn at ${LIVE_DISPLAY_HZ}` : ""
            }`,
          ]
        : [];
    })
    .join(". ");

  return (
    <div className="daq-viewer__watch">
      <div className="daq-viewer__controls">
        <ChannelToggles hidden={hidden} names={names} onToggle={toggle} />
        <LayoutToggle layout={layout} many={series.length > 1} onChange={setLayout}>
          {onPopOut && (
            <Button
              onClick={onPopOut}
              size="small"
              title="Open the graphs in a window of their own"
            >
              Open in a window
            </Button>
          )}
        </LayoutToggle>
      </div>
      {traces.some((trace) => trace.isError) &&
        instruments
          .filter((_, index) => traces[index].isError)
          .map((instrument) => (
            <EmptyState
              key={instrument}
              title="Not streaming"
              message={`${instrument} is not delivering scans.`}
            />
          ))}
      <p className="daq-viewer__note">
        {rates}
        {rates && ", "}last {TRACE_WINDOW_S} s
      </p>
      {layout === "stacked" ? (
        <div className={clsx("daq-viewer__trace", flash)}>
          <SeriesChart domain={LIVE_DOMAIN} hidden={hidden} order={names} series={series} />
        </div>
      ) : (
        series
          .filter((line) => !hidden.includes(line.name))
          .map((line) => (
            <div className={clsx("daq-viewer__trace", flash)} key={line.name}>
              <p className="daq-viewer__note">{line.name}</p>
              <SeriesChart
                domain={LIVE_DOMAIN}
                height={SEPARATE_HEIGHT}
                hidden={hidden}
                order={names}
                series={[line]}
              />
            </div>
          ))
      )}
    </div>
  );
};

/**
 * DAQ: what a streaming instrument read around each time it left its limits.
 *
 * While the run is in flight the last seconds of the stream are drawn from a
 * polled cursor and an operator sets the limits. Once it is over only the
 * viewer remains: the events, and the window around each, which can be
 * zoomed and scrolled. Nothing here names an instrument or a channel: both
 * come from what the stream reports.
 */
export const DaqViewer: React.FC<DaqViewerProps> = ({
  flashing,
  live,
  onPopOut,
  runId,
  summary,
}) => (
  <div className="daq-viewer">
    {summary.stopped_run && (
      <p className="daq-viewer__stopped" role="status">
        Stopped after {summary.events.length} {summary.events.length === 1 ? "event" : "events"}.
      </p>
    )}
    {live && summary.instruments.length > 0 && (
      <LiveTraces
        flashing={flashing}
        instruments={summary.instruments}
        onPopOut={onPopOut}
        runId={runId}
      />
    )}
    {!live && <DaqRecording events={summary.events} onPopOut={onPopOut} runId={runId} />}
    <EventList events={summary.events} runId={runId} stopAfter={summary.stop_after} />
  </div>
);

export default DaqViewer;

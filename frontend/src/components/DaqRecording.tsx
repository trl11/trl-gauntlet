import { keepPreviousData, useQueries, useQuery } from "@tanstack/react-query";
import { Button, Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useRef, useState } from "react";

import { getDaqRecording, getDaqWindow } from "@api/client";
import type { UpsetEntry } from "@api/types";
import {
  AXIS_WIDTH,
  ChannelToggles,
  LayoutToggle,
  RIGHT_MARGIN,
  SeriesChart,
  type Series,
} from "@components/DaqChart";
import useHidden from "@hooks/useHidden";
import useLayout from "@hooks/useLayout";
import { fitView, seriesOf, WINDOW_POINTS } from "../utils/daq_window";
import { formatDuration, formatNumber } from "../utils/format";

import "./DaqRecording.scss";

/** Props for {@link DaqRecording}. */
export interface DaqRecordingProps {
  /** Events the monitor recorded, marked on the graph where they happened. */
  events: UpsetEntry[];
  /** Opens the DAQ view in a window of its own, from a key beside the layout choice. */
  onPopOut?: () => void;
  /** Run whose recording is shown. */
  runId: string;
}

/** Height of a graph that has a channel to itself. */
const SEPARATE_HEIGHT = 150;

/** A drag across the graph, as fractions of the plot's width. */
interface Drag {
  from: number;
  to: number;
}

/** A drag shorter than this fraction of the plot is a click, not a zoom. */
const MIN_DRAG = 0.01;

/**
 * Everything a streaming instrument read during a run, as it was read.
 *
 * Drag across the graph to zoom to that stretch; the buttons zoom out, move
 * along the run and go back to all of it. A view with more scans than a graph
 * can draw is shown as an envelope of the lowest and highest reading in each
 * stretch, so a spike is there however far out the view is, and zooming in
 * brings the scans themselves.
 */
export const DaqRecording: React.FC<DaqRecordingProps> = ({ events, onPopOut, runId }) => {
  const [hidden, toggle] = useHidden();
  const [view, setView] = useState<[number, number] | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const plot = useRef<HTMLDivElement>(null);

  const recording = useQuery({
    queryKey: ["daq-recording", runId],
    queryFn: () => getDaqRecording(runId),
  });
  const instruments = recording.data?.instruments ?? [];
  const span: [number, number] = [
    Math.min(0, ...instruments.map((entry) => entry.start_s)),
    Math.max(...instruments.map((entry) => entry.end_s), 0),
  ];
  const range = view ?? span;

  const windows = useQueries({
    queries: instruments.map((entry) => ({
      queryKey: ["daq-window", runId, entry.instrument, range[0], range[1]],
      queryFn: () => getDaqWindow(runId, entry.instrument, range[0], range[1], WINDOW_POINTS),
      placeholderData: keepPreviousData,
    })),
  });

  const [layout, setLayout] = useLayout(
    instruments.reduce((total, entry) => total + entry.channels.length, 0)
  );

  if (recording.isPending) return <Spinner className="daq-recording__spinner" />;
  if (instruments.length === 0) return null;

  const several = instruments.length > 1;
  const series: Series[] = windows.flatMap((window) =>
    window.data
      ? seriesOf(window.data, (label) => (several ? `${window.data.instrument} · ${label}` : label))
      : []
  );
  const names = series.map((line) => line.name);
  const enveloped = windows.some((window) =>
    window.data?.segments.some((segment) => segment.kind === "envelope")
  );
  const origin = recording.data?.origin ?? 0;
  const markers = events.map((event) => Date.parse(event.at) / 1000 - origin);

  const zoomTo = (next: [number, number]) => setView(fitView(next, span));
  const width = range[1] - range[0];
  const middle = (range[0] + range[1]) / 2;

  const timeAt = (fraction: number) => range[0] + fraction * width;
  const fractionOf = (clientX: number): number => {
    const box = plot.current?.getBoundingClientRect();
    if (!box) return 0;
    const inner = box.width - AXIS_WIDTH - RIGHT_MARGIN;
    return Math.min(1, Math.max(0, (clientX - box.left - AXIS_WIDTH) / Math.max(1, inner)));
  };

  const charts =
    layout === "stacked" ? (
      <SeriesChart domain={range} hidden={hidden} markers={markers} order={names} series={series} />
    ) : (
      series
        .filter((line) => !hidden.includes(line.name))
        .map((line) => (
          <div className="daq-recording__panel" key={line.name}>
            <p className="daq-recording__note">{line.name}</p>
            <SeriesChart
              domain={range}
              height={SEPARATE_HEIGHT}
              hidden={hidden}
              markers={markers}
              order={names}
              series={[line]}
            />
          </div>
        ))
    );

  return (
    <div className="daq-recording">
      <div className="daq-recording__controls">
        <ChannelToggles hidden={hidden} names={names} onToggle={toggle} />
        <div className="daq-recording__buttons">
          <Button
            disabled={width <= 0}
            onClick={() => zoomTo([middle - width / 4, middle + width / 4])}
            size="small"
          >
            Zoom in
          </Button>
          <Button
            disabled={view === null}
            onClick={() => zoomTo([middle - width, middle + width])}
            size="small"
          >
            Zoom out
          </Button>
          <Button
            aria-label="Earlier"
            disabled={view === null}
            onClick={() => zoomTo([range[0] - width / 2, range[1] - width / 2])}
            size="small"
          >
            ◀
          </Button>
          <Button
            aria-label="Later"
            disabled={view === null}
            onClick={() => zoomTo([range[0] + width / 2, range[1] + width / 2])}
            size="small"
          >
            ▶
          </Button>
          <Button disabled={view === null} onClick={() => setView(null)} size="small">
            All
          </Button>
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
      </div>

      <p className="daq-recording__note">
        {instruments
          .map(
            (entry) =>
              `${several ? `${entry.instrument}: ` : ""}${formatNumber(entry.rows, 0)} scans at ${formatNumber(entry.rate_hz)} per second over ${formatDuration(entry.end_s - entry.start_s)}`
          )
          .join(". ")}
        . Showing {formatNumber(range[0], 3)} s to {formatNumber(range[1], 3)} s
        {enveloped
          ? ", as the lowest and highest reading in each stretch. Drag across the graph to zoom in."
          : ", every scan."}
      </p>

      <div
        className={clsx("daq-recording__plot", drag && "daq-recording__plot--dragging")}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture?.(event.pointerId);
          const at = fractionOf(event.clientX);
          setDrag({ from: at, to: at });
        }}
        onPointerMove={(event) => drag && setDrag({ ...drag, to: fractionOf(event.clientX) })}
        onPointerUp={() => {
          if (drag && Math.abs(drag.to - drag.from) >= MIN_DRAG) {
            zoomTo([timeAt(Math.min(drag.from, drag.to)), timeAt(Math.max(drag.from, drag.to))]);
          }
          setDrag(null);
        }}
        ref={plot}
      >
        {charts}
        {drag && Math.abs(drag.to - drag.from) >= MIN_DRAG && (
          <div
            className="daq-recording__selection"
            style={{
              left: `calc(${AXIS_WIDTH}px + (100% - ${AXIS_WIDTH + RIGHT_MARGIN}px) * ${Math.min(drag.from, drag.to)})`,
              width: `calc((100% - ${AXIS_WIDTH + RIGHT_MARGIN}px) * ${Math.abs(drag.to - drag.from)})`,
            }}
          />
        )}
      </div>
    </div>
  );
};

export default DaqRecording;

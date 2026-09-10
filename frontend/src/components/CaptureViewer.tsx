import { useQuery } from "@tanstack/react-query";
import { Button, Input, Select, Spinner } from "@trl11/components/ui";
import { useId, useMemo, useState } from "react";
import {
  Brush,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { artifactUrl, getArtifactText } from "@api/client";
import EmptyState from "@components/EmptyState";
import { formatNumber } from "../utils/format";
import { decimate, parseCapture, spanOf } from "../utils/capture";

import "./CaptureViewer.scss";

/** Points drawn at once. Above this the window is thinned to its envelope. */
const BUDGET = 1200;

/** How many colours the stylesheet defines for traces. */
const COLOR_COUNT = 5;

/** Props for {@link CaptureViewer}. */
export interface CaptureViewerProps {
  /** Capture files in the run directory, in the order they were written. */
  paths: string[];
  /** Run the captures belong to. */
  runId: string;
}

/** The iteration a capture belongs to, from its filename. */
function iterationOf(path: string): string {
  const digits = path.match(/(\d+)\.csv$/);
  return digits ? String(Number(digits[1])) : path;
}

/**
 * One captured waveform, drawn.
 *
 * The samples are a file the run left behind, so this asks nothing of the
 * instrument and reads the same for a run finished months ago. Nothing here
 * knows a channel: the file's header names them.
 *
 * Zooming is what the viewer is for. A capture holds thousands of samples and
 * a chart is hundreds of pixels wide, so the whole window is drawn as an
 * envelope — every bucket's lowest and highest sample — and a window zoomed
 * far enough in is drawn sample for sample.
 */
export const CaptureViewer: React.FC<CaptureViewerProps> = ({ paths, runId }) => {
  const fieldId = useId();
  const [selected, setSelected] = useState(paths[0] ?? "");
  const [window, setWindow] = useState<[number, number] | null>(null);
  const [low, setLow] = useState("");
  const [high, setHigh] = useState("");
  const [hidden, setHidden] = useState<string[]>([]);

  const path = paths.includes(selected) ? selected : (paths[0] ?? "");
  const file = useQuery({
    queryKey: ["capture", runId, path],
    queryFn: () => getArtifactText(runId, path),
    enabled: path !== "",
  });

  const capture = useMemo(() => parseCapture(file.data ?? ""), [file.data]);
  const depth = capture.times.length;
  const [from, to] = window ?? [0, Math.max(0, depth - 1)];
  const points = useMemo(() => decimate(capture, from, to, BUDGET), [capture, from, to]);
  const drawn = capture.channels.filter((name) => !hidden.includes(name));

  if (file.isPending && path !== "") return <Spinner className="capture-viewer__spinner" />;

  if (file.isError || depth === 0) {
    return (
      <EmptyState
        title="No samples"
        message={`Nothing could be read from ${path || "this run's captures"}.`}
      />
    );
  }

  const shownSamples = to - from + 1;
  const period = depth > 1 ? capture.times[1] - capture.times[0] : 0;
  const rate = period > 0 ? 1 / period : 0;

  return (
    <div className="capture-viewer">
      <div className="capture-viewer__controls">
        <Select
          id={`${fieldId}-capture`}
          label="Capture"
          options={paths.map((entry) => ({
            value: entry,
            label: `Iteration ${iterationOf(entry)}`,
          }))}
          value={path}
          onChange={(event) => {
            setSelected(event.target.value);
            setWindow(null);
          }}
        />
        <Input
          id={`${fieldId}-low`}
          type="number"
          label="Y axis min"
          placeholder="auto"
          value={low}
          onChange={(event) => setLow(event.target.value)}
        />
        <Input
          id={`${fieldId}-high`}
          type="number"
          label="Y axis max"
          placeholder="auto"
          value={high}
          onChange={(event) => setHigh(event.target.value)}
        />
        <div className="capture-viewer__buttons">
          <Button
            size="small"
            onClick={() => {
              const spans = drawn
                .map((name) => spanOf(capture, name))
                .filter((span) => span !== null);
              if (spans.length === 0) return;
              setLow(String(Math.min(...spans.map(([min]) => min))));
              setHigh(String(Math.max(...spans.map(([, max]) => max))));
            }}
          >
            Fit
          </Button>
          <Button
            size="small"
            onClick={() => {
              setLow("");
              setHigh("");
              setWindow(null);
            }}
          >
            Reset
          </Button>
        </div>
      </div>

      <p className="capture-viewer__note">
        {[
          `${formatNumber(depth, 0)} samples at ${formatNumber(rate / 1000)} kS/s`,
          shownSamples < depth ? `showing ${formatNumber(shownSamples, 0)} of them` : "all shown",
          shownSamples > BUDGET ? "as an envelope — zoom in for sample-for-sample" : "",
        ]
          .filter((part) => part !== "")
          .join(", ")}
        . <a href={artifactUrl(runId, path)}>Download this capture</a>
      </p>

      <div className="capture-viewer__channels">
        {capture.channels.map((name, index) => (
          <Button
            key={name}
            size="small"
            className={`capture-viewer__channel capture-viewer__channel--c${index % COLOR_COUNT}`}
            color={hidden.includes(name) ? "transparent" : undefined}
            aria-pressed={!hidden.includes(name)}
            onClick={() =>
              setHidden((current) =>
                current.includes(name)
                  ? current.filter((entry) => entry !== name)
                  : [...current, name]
              )
            }
          >
            {name}
          </Button>
        ))}
      </div>

      <div className="capture-viewer__chart">
        <ResponsiveContainer width="100%" height={360}>
          <LineChart data={points} margin={{ top: 8, right: 16, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              allowDataOverflow
              dataKey="t"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(value: number) => `${formatNumber(value * 1000, 3)}ms`}
              type="number"
            />
            <YAxis
              allowDataOverflow
              domain={[
                low.trim() === "" ? "dataMin" : Number(low),
                high.trim() === "" ? "dataMax" : Number(high),
              ]}
              tickFormatter={(value: number) => formatNumber(value, 6)}
              width={80}
            />
            <ChartTooltip
              formatter={(value: number | string) => formatNumber(Number(value), 6)}
              labelFormatter={(value: number | string) =>
                `${formatNumber(Number(value) * 1000, 4)} ms`
              }
            />
            {drawn.map((name) => (
              <Line
                key={name}
                className={`capture-viewer__line capture-viewer__line--c${capture.channels.indexOf(name) % COLOR_COUNT}`}
                dataKey={name}
                dot={false}
                isAnimationActive={false}
                stroke="currentColor"
                strokeWidth={1.2}
                type="linear"
              />
            ))}
            <Brush
              dataKey="t"
              height={22}
              tickFormatter={(value: number) => `${formatNumber(value * 1000, 2)}ms`}
              onChange={(next: { startIndex?: number; endIndex?: number }) => {
                const first = points[next.startIndex ?? 0];
                const last = points[next.endIndex ?? points.length - 1];
                if (!first || !last) return;
                // The brush indexes what is drawn, and what is drawn is an
                // envelope of the samples, so the window it names is turned
                // back into sample numbers before the next draw thins them.
                const startAt = capture.times.findIndex((t) => t >= first.t);
                const endAt = capture.times.findIndex((t) => t >= last.t);
                setWindow([
                  startAt < 0 ? 0 : startAt,
                  endAt < 0 ? capture.times.length - 1 : endAt,
                ]);
              }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default CaptureViewer;

import { faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Button, Input, Tooltip } from "@trl11/components/ui";
import clsx from "clsx";
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

import SeriesPicker from "@components/SeriesPicker";
import usePersistedSeries from "@hooks/usePersistedSeries";
import { formatNumber } from "../utils/format";
import { naturalCompare } from "../utils/metrics";

import "./MetricsChart.scss";

/** How many colours the stylesheet defines for chart panels. */
const COLOR_COUNT = 5;

/** Series charted before the operator picks their own, when the suite declares none. */
const DEFAULT_SERIES = 4;

/** One flattened metrics record, live or replayed from `metrics.jsonl`. */
export interface MetricSample {
  elapsed_s: number | null;
  iteration: number | null;
  seq: number;
  ts: number;
  values: Record<string, number>;
}

/** Props for {@link MetricsChart}. */
export interface MetricsChartProps {
  /** Run this chart belongs to, to scope the persisted series pick. */
  runId: string;
  /** Samples in arrival order. */
  samples: MetricSample[];
  /**
   * Series the suite declares as worth charting by default, in
   * `suite.yaml`'s `default_metrics`. Only those the run actually reported
   * are used; falls back to the first few reported series when none apply.
   */
  defaultMetrics: string[];
}

interface Row {
  x: number;
  [series: string]: number;
}

/** The vertical window one series is drawn in. Empty means the chart decides. */
interface Bounds {
  max: string;
  min: string;
}

/** A bound as recharts wants it: a number, or the data's own edge. */
function edge(value: string, fallback: "dataMin" | "dataMax"): number | string {
  const parsed = Number(value);
  return value.trim() !== "" && Number.isFinite(parsed) ? parsed : fallback;
}

/** The lowest and highest a series reached, for the fit button. */
function span(rows: Row[], name: string): [number, number] | null {
  const values = rows.map((row) => row[name]).filter((value) => typeof value === "number");
  return values.length > 0 ? [Math.min(...values), Math.max(...values)] : null;
}

/** Seconds since the first sample, preferring the elapsed time the suite reported. */
function elapsed(sample: MetricSample, firstTs: number): number {
  if (sample.elapsed_s != null) return sample.elapsed_s;
  return sample.ts - firstTs;
}

/**
 * One chart per numeric series the run emitted.
 *
 * Series names come from the data, never from a list of known metrics, so a
 * suite can publish anything and it plots. Every chart shares a `syncId`, which
 * is what gives them one crosshair and one tooltip position. Which series are
 * charted persists per run in `localStorage`, so leaving and returning to a
 * run keeps the operator's picks.
 */
export const MetricsChart: React.FC<MetricsChartProps> = ({ runId, samples, defaultMetrics }) => {
  const fieldId = useId();
  const [chosen, setChosen] = usePersistedSeries(`gauntlet:run:${runId}:metrics-series`);
  const [range, setRange] = useState<[number, number] | null>(null);
  // The vertical window per series. A run charting millivolts beside volts
  // needs one axis narrowed and the other left alone, so the window belongs to
  // the series rather than to the chart.
  const [bounds, setBounds] = useState<Record<string, Bounds>>({});

  const names = useMemo(() => {
    const seen = new Set<string>();
    for (const sample of samples) {
      for (const name of Object.keys(sample.values)) seen.add(name);
    }
    return [...seen].sort(naturalCompare);
  }, [samples]);

  const rows = useMemo<Row[]>(() => {
    if (samples.length === 0) return [];
    const firstTs = samples[0].ts;
    return samples.map((sample) => ({ x: elapsed(sample, firstTs), ...sample.values }));
  }, [samples]);

  const setBound = (name: string, edgeName: keyof Bounds, value: string) =>
    setBounds((current) => ({
      ...current,
      [name]: { ...(current[name] ?? { max: "", min: "" }), [edgeName]: value },
    }));

  const reported = defaultMetrics.filter((name) => names.includes(name));
  const selected = chosen ?? (reported.length > 0 ? reported : names.slice(0, DEFAULT_SERIES));
  const remove = (name: string) => setChosen(selected.filter((entry) => entry !== name));

  if (names.length === 0) {
    return (
      <p className="metrics-chart__empty">No numeric metrics have been reported for this run.</p>
    );
  }

  return (
    <section className="metrics-chart" aria-label="Metrics">
      <div className="metrics-chart__pick">
        <SeriesPicker names={names} selected={selected} onChange={setChosen} />
        {range && (
          <Button size="small" onClick={() => setRange(null)}>
            Reset zoom
          </Button>
        )}
      </div>

      {selected.length === 0 ? (
        <p className="metrics-chart__empty">Pick a series to chart it.</p>
      ) : (
        selected.map((name, index) => (
          <div
            key={name}
            className={clsx(
              "metrics-chart__panel",
              `metrics-chart__panel--c${index % COLOR_COUNT}`
            )}
          >
            <div className="metrics-chart__header">
              <h3 className="metrics-chart__title">{name}</h3>
              <div className="metrics-chart__axis">
                <Input
                  id={`${fieldId}-${name}-min`}
                  type="number"
                  className="metrics-chart__bound"
                  aria-label={`Lowest value charted for ${name}`}
                  placeholder="min"
                  value={bounds[name]?.min ?? ""}
                  onChange={(event) => setBound(name, "min", event.target.value)}
                />
                <Input
                  id={`${fieldId}-${name}-max`}
                  type="number"
                  className="metrics-chart__bound"
                  aria-label={`Highest value charted for ${name}`}
                  placeholder="max"
                  value={bounds[name]?.max ?? ""}
                  onChange={(event) => setBound(name, "max", event.target.value)}
                />
                <Tooltip content="Fit the axis to this series">
                  <Button
                    size="small"
                    aria-label={`Fit the axis to ${name}`}
                    onClick={() => {
                      const reached = span(rows, name);
                      if (reached)
                        setBounds((current) => ({
                          ...current,
                          [name]: { max: String(reached[1]), min: String(reached[0]) },
                        }));
                    }}
                  >
                    Fit
                  </Button>
                </Tooltip>
              </div>
              <Tooltip content="Remove from chart">
                <Button
                  className="metrics-chart__remove"
                  size="small"
                  square
                  color="transparent"
                  aria-label={`Remove ${name} from chart`}
                  onClick={() => remove(name)}
                >
                  <FontAwesomeIcon icon={faXmark} />
                </Button>
              </Tooltip>
            </div>
            <ResponsiveContainer width="100%" height={index === selected.length - 1 ? 210 : 170}>
              <LineChart data={rows} syncId="run-metrics" margin={{ top: 4, right: 12, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  allowDataOverflow
                  dataKey="x"
                  domain={range ?? ["dataMin", "dataMax"]}
                  tickFormatter={(value: number) => `${formatNumber(value, 1)}s`}
                  type="number"
                />
                <YAxis
                  allowDataOverflow
                  domain={[
                    edge(bounds[name]?.min ?? "", "dataMin"),
                    edge(bounds[name]?.max ?? "", "dataMax"),
                  ]}
                  tickFormatter={(value: number) => formatNumber(value)}
                  width={64}
                />
                <ChartTooltip
                  formatter={(value: number | string) => formatNumber(Number(value))}
                  labelFormatter={(value: number | string) => `${formatNumber(Number(value), 2)} s`}
                />
                <Line
                  dataKey={name}
                  dot={false}
                  isAnimationActive={false}
                  stroke="currentColor"
                  strokeWidth={1.5}
                  type="monotone"
                />
                {index === selected.length - 1 && (
                  <Brush
                    dataKey="x"
                    height={22}
                    tickFormatter={(value: number) => `${formatNumber(value, 1)}s`}
                    onChange={(next: { startIndex?: number; endIndex?: number }) => {
                      const from = rows[next.startIndex ?? 0];
                      const to = rows[next.endIndex ?? rows.length - 1];
                      if (from && to) setRange([from.x, to.x]);
                    }}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        ))
      )}
    </section>
  );
};

export default MetricsChart;

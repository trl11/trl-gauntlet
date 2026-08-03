import { Button, Checkbox } from "@trl11/components/ui";
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

import { formatNumber } from "../utils/format";

import "./MetricsChart.scss";

/** How many colours the stylesheet defines for chart panels. */
const COLOR_COUNT = 5;

/** Series charted before the operator picks their own. */
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
  /** Samples in arrival order. */
  samples: MetricSample[];
}

interface Row {
  x: number;
  [series: string]: number;
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
 * is what gives them one crosshair and one tooltip position.
 */
export const MetricsChart: React.FC<MetricsChartProps> = ({ samples }) => {
  const fieldId = useId();
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [range, setRange] = useState<[number, number] | null>(null);

  const names = useMemo(() => {
    const seen = new Set<string>();
    for (const sample of samples) {
      for (const name of Object.keys(sample.values)) seen.add(name);
    }
    return [...seen].sort();
  }, [samples]);

  const rows = useMemo<Row[]>(() => {
    if (samples.length === 0) return [];
    const firstTs = samples[0].ts;
    return samples.map((sample) => ({ x: elapsed(sample, firstTs), ...sample.values }));
  }, [samples]);

  const selected = chosen ?? names.slice(0, DEFAULT_SERIES);

  const toggle = (name: string) => {
    const next = selected.includes(name)
      ? selected.filter((entry) => entry !== name)
      : [...selected, name];
    setChosen(next);
  };

  if (names.length === 0) {
    return (
      <p className="metrics-chart__empty">No numeric metrics have been reported for this run.</p>
    );
  }

  return (
    <section className="metrics-chart" aria-label="Metrics">
      <div className="metrics-chart__series">
        {names.map((name) => (
          <Checkbox
            key={name}
            id={`${fieldId}-${name}`}
            label={name}
            checked={selected.includes(name)}
            onChange={() => toggle(name)}
          />
        ))}
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
            <h3 className="metrics-chart__title">{name}</h3>
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
                <YAxis tickFormatter={(value: number) => formatNumber(value)} width={64} />
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

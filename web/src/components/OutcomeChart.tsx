import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import EmptyState from "@components/EmptyState";

import "./OutcomeChart.scss";

/** Run outcomes counted over one time window. */
export interface OutcomeBucket {
  /** Runs that ended in `failed`. */
  failed: number;
  /** Name of the window, such as "Last 24h". */
  label: string;
  /** Runs that ended in `aborted`, `error` or `interrupted`. */
  other: number;
  /** Runs that ended in `passed`. */
  passed: number;
}

/** Props for {@link OutcomeChart}. */
export interface OutcomeChartProps {
  /** One entry per time window, left to right. */
  buckets: OutcomeBucket[];
}

/** Series drawn for every bucket, in a fixed order so a colour never moves. */
const SERIES = [
  { fill: "var(--outcome-passed)", key: "passed", label: "Passed" },
  { fill: "var(--outcome-failed)", key: "failed", label: "Failed" },
  { fill: "var(--outcome-other)", key: "other", label: "Other" },
] as const;

function describe(bucket: OutcomeBucket): string {
  return `${bucket.label}: ${bucket.passed} passed, ${bucket.failed} failed, ${bucket.other} other`;
}

/** Pass, fail and other counts per time window, as grouped bars. */
export const OutcomeChart: React.FC<OutcomeChartProps> = ({ buckets }) => {
  const total = buckets.reduce(
    (sum, bucket) => sum + bucket.passed + bucket.failed + bucket.other,
    0
  );

  if (total === 0) {
    return (
      <EmptyState
        className="outcome-chart"
        title="No outcomes yet"
        message="Runs finished in these windows will be counted here."
      />
    );
  }

  return (
    <figure className="outcome-chart">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={buckets} margin={{ bottom: 0, left: 0, right: 8, top: 16 }}>
          <CartesianGrid stroke="var(--outcome-grid)" vertical={false} />
          <XAxis dataKey="label" stroke="var(--outcome-axis)" tickLine={false} fontSize={12} />
          <YAxis
            allowDecimals={false}
            stroke="var(--outcome-axis)"
            tickLine={false}
            axisLine={false}
            width={32}
            fontSize={12}
          />
          <Tooltip
            cursor={{ fill: "var(--outcome-grid)" }}
            contentStyle={{
              backgroundColor: "var(--outcome-surface)",
              border: "1px solid var(--outcome-grid)",
              fontSize: 12,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {SERIES.map((series) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              name={series.label}
              fill={series.fill}
              radius={[4, 4, 0, 0]}
              maxBarSize={36}
            >
              <LabelList dataKey={series.key} position="top" fill="var(--outcome-label)" />
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
      <figcaption className="outcome-chart__caption">
        {buckets.map(describe).join(" · ")}
      </figcaption>
    </figure>
  );
};

export default OutcomeChart;

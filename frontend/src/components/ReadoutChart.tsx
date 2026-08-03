import clsx from "clsx";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, XAxis, YAxis } from "recharts";

import { formatNumber } from "../utils/format";
import { tickDecimals } from "../utils/readouts";

import "./ReadoutChart.scss";

/** How many colours the stylesheet defines for series lines. */
const COLOR_COUNT = 5;

/** One line on the chart. */
export interface ReadoutSeries {
  /** Key the samples in `history` are recorded under. */
  key: string;
  label: string;
}

/** Props for {@link ReadoutChart}. */
export interface ReadoutChartProps {
  /** One entry per poll, oldest first, each keyed by series key. */
  history: Array<Record<string, number>>;
  series: ReadoutSeries[];
}

/**
 * The recent history of a few numeric readings, as a rolling line chart.
 *
 * Series come from whatever the caller passes, so the chart knows nothing
 * about the instrument behind them. Two samples are needed before a line has
 * anything to draw.
 */
export const ReadoutChart: React.FC<ReadoutChartProps> = ({ history, series }) => {
  if (series.length === 0 || history.length < 2) return null;

  const decimals = tickDecimals(
    history,
    series.map((entry) => entry.key)
  );

  return (
    <div className="readout-chart">
      <ResponsiveContainer width="100%" height={110}>
        <LineChart data={history} margin={{ bottom: 0, left: 0, right: 8, top: 8 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey={undefined} hide />
          <YAxis
            tickFormatter={(value: number) => formatNumber(value, decimals)}
            width={52}
            domain={["auto", "auto"]}
          />
          {series.map((entry, index) => (
            <Line
              className={clsx(
                "readout-chart__line",
                `readout-chart__line--c${index % COLOR_COUNT}`
              )}
              dataKey={entry.key}
              dot={false}
              isAnimationActive={false}
              key={entry.key}
              name={entry.label}
              stroke="currentColor"
              strokeWidth={1.5}
              type="monotone"
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <ul className="readout-chart__legend">
        {series.map((entry, index) => (
          <li key={entry.key} className={`readout-chart__line--c${index % COLOR_COUNT}`}>
            <span className="readout-chart__swatch" aria-hidden="true" />
            {entry.label}
          </li>
        ))}
      </ul>
    </div>
  );
};

export default ReadoutChart;

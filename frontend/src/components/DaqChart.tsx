import { Button } from "@trl11/components/ui";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import type { Layout } from "@hooks/useLayout";
import { formatNumber } from "../utils/format";
import { verticalDomain } from "../utils/metrics";

import "./DaqChart.scss";

/** How many colours the stylesheet defines for traces. */
export const COLOR_COUNT = 5;

/** Width of the vertical axis, which a drag over the chart has to allow for. */
export const AXIS_WIDTH = 72;

/** Space to the right of the plot, for the same reason. */
export const RIGHT_MARGIN = 16;

/** One point of a line: seconds on the horizontal axis, a reading on the vertical. */
export interface Point {
  t: number;
  v: number;
}

/** One channel of one instrument. */
export interface Series {
  instrument: string;
  /** The highest reading over a longer span than `points` holds, such as the whole run. */
  max?: number;
  /** The lowest reading over that span. */
  min?: number;
  name: string;
  points: Point[];
  /** The least and most the instrument can read on the channel, which fixes the vertical axis. */
  range?: [number, number];
  unit?: string;
}

/** One button per channel, each lit while its line is drawn. */
export const ChannelToggles: React.FC<{
  hidden: string[];
  names: string[];
  onToggle: (name: string) => void;
}> = ({ hidden, names, onToggle }) => (
  <div className="daq-chart__channels">
    {names.map((name, index) => (
      <Button
        aria-pressed={!hidden.includes(name)}
        className={`daq-chart__channel daq-chart__channel--c${index % COLOR_COUNT}`}
        color={hidden.includes(name) ? "transparent" : undefined}
        key={name}
        onClick={() => onToggle(name)}
        size="small"
      >
        {name}
      </Button>
    ))}
  </div>
);

/** The buttons that choose how lines share the page, with any keys that go beside them. */
export const LayoutToggle: React.FC<{
  children?: React.ReactNode;
  layout: Layout;
  /** Whether there is more than one line to arrange. */
  many: boolean;
  onChange: (choice: Layout) => void;
}> = ({ children, layout, many, onChange }) => (
  <div className="daq-chart__layout" role="group" aria-label="Layout">
    {many &&
      (["separate", "stacked"] as const).map((choice) => (
        <Button
          aria-pressed={layout === choice}
          color={layout === choice ? undefined : "transparent"}
          key={choice}
          onClick={() => onChange(choice)}
          size="small"
        >
          {choice === "separate" ? "Separate" : "Stacked"}
        </Button>
      ))}
    {children}
  </div>
);

/** Props for {@link SeriesChart}. */
export interface SeriesChartProps {
  /** Seconds the horizontal axis spans, from first to last. */
  domain: [number, number];
  /** Height of the graph in pixels. */
  height?: number;
  /** Names to leave out. */
  hidden: string[];
  /** Seconds to mark with a vertical line. */
  markers?: number[];
  /** Every name in colour order, so a line keeps its colour whichever are showing. */
  order: string[];
  series: Series[];
}

/** Every line drawn on one set of axes, the vertical one framing what is showing. */
export const SeriesChart: React.FC<SeriesChartProps> = ({
  domain,
  height = 240,
  hidden,
  markers = [],
  order,
  series,
}) => {
  const drawn = series.filter((line) => !hidden.includes(line.name));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart margin={{ top: 8, right: RIGHT_MARGIN, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis
          allowDataOverflow
          dataKey="t"
          domain={domain}
          tickFormatter={(value: number) => `${formatNumber(value, 2)}s`}
          type="number"
        />
        <YAxis
          domain={verticalDomain(drawn)}
          tickFormatter={(value: number) => formatNumber(value, 4)}
          width={AXIS_WIDTH}
        />
        {markers
          .filter((at) => at >= domain[0] && at <= domain[1])
          .map((at) => (
            <ReferenceLine className="daq-chart__marker" key={at} x={at} />
          ))}
        {drawn.map((line) => (
          <Line
            className={`daq-chart__line daq-chart__line--c${order.indexOf(line.name) % COLOR_COUNT}`}
            data={line.points}
            dataKey="v"
            dot={false}
            isAnimationActive={false}
            key={line.name}
            name={line.name}
            stroke="currentColor"
            strokeWidth={1}
            type="linear"
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
};

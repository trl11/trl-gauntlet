import clsx from "clsx";

import "./StatTile.scss";

/** How urgent the figure is, or "highlight" for a featured figure with no urgency. Drives the colour of the value and the meter. */
export type StatTone = "critical" | "highlight" | "normal" | "warning";

/** Props for {@link StatTile}. */
export interface StatTileProps {
  /** Secondary line under the value, such as "12.4 GB of 32 GB". */
  detail?: React.ReactNode;
  /** What the figure measures. */
  label: string;
  /** 0-100. Draws a meter under the value when set. */
  percent?: number | null;
  /** Recent readings, oldest first. Two or more draw a sparkline. */
  samples?: number[];
  tone?: StatTone;
  /** The headline figure, already formatted. */
  value: React.ReactNode;
}

/** Polyline points for a sparkline drawn in a 100 by 24 viewBox. */
function sparklinePoints(samples: number[]): string {
  const high = Math.max(...samples);
  const low = Math.min(...samples);
  const span = high - low || 1;
  const step = 100 / (samples.length - 1);
  return samples
    .map((sample, index) => {
      const x = index * step;
      const y = 23 - ((sample - low) / span) * 22;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/** One host-health figure: a value, an optional meter and an optional sparkline. */
export const StatTile: React.FC<StatTileProps> = ({
  detail,
  label,
  percent,
  samples = [],
  tone = "normal",
  value,
}) => {
  const meter =
    percent != null && Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : null;

  return (
    <div className={clsx("stat-tile", `stat-tile--${tone}`)}>
      <p className="stat-tile__label">{label}</p>
      <p className="stat-tile__value">{value}</p>
      {detail && <p className="stat-tile__detail">{detail}</p>}
      {meter !== null && (
        <div
          className="stat-tile__meter"
          role="progressbar"
          aria-label={`${label} usage`}
          aria-valuenow={Math.round(meter)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span className="stat-tile__meter-fill" style={{ width: `${meter}%` }} />
        </div>
      )}
      {samples.length > 1 && (
        <svg
          className="stat-tile__spark"
          viewBox="0 0 100 24"
          preserveAspectRatio="none"
          aria-hidden="true"
          focusable="false"
        >
          <polyline points={sparklinePoints(samples)} />
        </svg>
      )}
    </div>
  );
};

export default StatTile;

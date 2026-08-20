import "./Sparkline.scss";

/** The box the line is drawn in, in the SVG's own units. */
const WIDTH = 64;
const HEIGHT = 18;

/** Kept clear at the top and bottom so the stroke is not clipped at the extremes. */
const INSET = 1.5;

/** Props for {@link Sparkline}. */
export interface SparklineProps {
  /** Samples oldest first. Two are needed before a line has anything to draw. */
  values: number[];
}

/**
 * The recent history of one reading, small enough to sit beside it.
 *
 * The line is scaled to its own highest and lowest sample rather than to any
 * absolute range, so it shows the shape of the movement and never the size of
 * it. A reading that has not moved is drawn along the middle, because a flat
 * line at the floor reads as a value that has fallen to nothing.
 */
export const Sparkline: React.FC<SparklineProps> = ({ values }) => {
  if (values.length < 2) return null;

  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low;
  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * WIDTH;
      const y =
        span === 0 ? HEIGHT / 2 : HEIGHT - INSET - ((value - low) / span) * (HEIGHT - INSET * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      aria-hidden="true"
      className="sparkline"
      preserveAspectRatio="none"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
    >
      <polyline className="sparkline__line" points={points} vectorEffect="non-scaling-stroke" />
    </svg>
  );
};

export default Sparkline;

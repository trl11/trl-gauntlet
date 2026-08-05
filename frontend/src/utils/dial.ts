/** Turning a rotary dial's angle into the setting it stands for. */

/** Degrees the dial turns between its lowest and its highest setting. */
export const SWEEP = 270;

export function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** Decimals `step` needs, so snapping does not leave floating point dust. */
export function decimalsOf(step: number): number {
  const text = String(step);
  const point = text.indexOf(".");
  return point === -1 ? 0 : text.length - point - 1;
}

/** The nearest setting to `value` that is a whole number of steps from `min`. */
export function snap(value: number, min: number, max: number, step: number): number {
  const stepped = min + Math.round((clamp(value, min, max) - min) / step) * step;
  return Number(clamp(stepped, min, max).toFixed(decimalsOf(step)));
}

/** How far along its travel a setting sits, as a fraction. */
export function fractionOf(value: number, min: number, max: number): number {
  if (max <= min) return 0;
  return clamp((value - min) / (max - min), 0, 1);
}

/**
 * The setting a point on the dial stands for.
 *
 * Angles are measured from the top and grow clockwise, so the lower part of
 * the circle, which the sweep does not reach, clamps to whichever end of the
 * range is nearer.
 */
export function valueAtPoint(
  box: { height: number; left: number; top: number; width: number },
  point: { x: number; y: number },
  min: number,
  max: number,
  step: number
): number {
  const fromCentre = {
    x: point.x - (box.left + box.width / 2),
    y: point.y - (box.top + box.height / 2),
  };
  const degrees = (Math.atan2(fromCentre.x, -fromCentre.y) * 180) / Math.PI;
  const fraction = (clamp(degrees, -SWEEP / 2, SWEEP / 2) + SWEEP / 2) / SWEEP;
  return snap(min + fraction * (max - min), min, max, step);
}

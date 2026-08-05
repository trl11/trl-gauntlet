import { useRef, useState } from "react";

import { fractionOf, snap, SWEEP, valueAtPoint } from "../utils/dial";

import "./Knob.scss";

/** Fraction of the whole circle the sweep covers, as an SVG dash length. */
const TRACK = (SWEEP / 360) * 100;

/** Props for {@link Knob}. */
export interface KnobProps {
  disabled: boolean;
  /** Names the dial for assistive technology; the visible label sits beside it. */
  label: string;
  max: number;
  min: number;
  onChange: (value: number) => void;
  /** How far one arrow key, or one snap of the dial, moves the setting. */
  step: number;
  value: number;
}

/**
 * A rotary dial, turned by dragging it, clicking a point on it, or by the
 * arrow keys.
 *
 * The dial knows nothing but a range and a step, so any field a provider
 * declares with both a minimum and a maximum can be set with one.
 */
export const Knob: React.FC<KnobProps> = ({ disabled, label, max, min, onChange, step, value }) => {
  const dial = useRef<HTMLDivElement>(null);
  const [turning, setTurning] = useState(false);

  const fraction = fractionOf(value, min, max);

  const turnTo = (event: React.PointerEvent) => {
    if (dial.current === null) return;
    const box = dial.current.getBoundingClientRect();
    onChange(valueAtPoint(box, { x: event.clientX, y: event.clientY }, min, max, step));
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    const moves: Record<string, number> = {
      ArrowDown: -step,
      ArrowLeft: -step,
      ArrowRight: step,
      ArrowUp: step,
      PageDown: -step * 10,
      PageUp: step * 10,
    };
    if (event.key === "Home") onChange(min);
    else if (event.key === "End") onChange(max);
    else if (event.key in moves) onChange(snap(value + moves[event.key], min, max, step));
    else return;
    event.preventDefault();
  };

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    // Capture keeps a drag on the dial once the pointer wanders off it, where
    // the environment offers it at all.
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setTurning(true);
    turnTo(event);
  };

  return (
    <div
      aria-disabled={disabled}
      aria-label={`${label} dial`}
      aria-valuemax={max}
      aria-valuemin={min}
      aria-valuenow={value}
      className="knob"
      onKeyDown={disabled ? undefined : onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={turning && !disabled ? turnTo : undefined}
      onPointerUp={() => setTurning(false)}
      ref={dial}
      role="slider"
      tabIndex={disabled ? -1 : 0}
    >
      <svg aria-hidden="true" className="knob__track" viewBox="0 0 100 100">
        <circle
          className="knob__arc"
          cx="50"
          cy="50"
          pathLength={100}
          r="46"
          strokeDasharray={`${TRACK} ${100 - TRACK}`}
        />
        <circle
          className="knob__arc knob__arc--set"
          cx="50"
          cy="50"
          pathLength={100}
          r="46"
          strokeDasharray={`${TRACK * fraction} 100`}
        />
      </svg>
      <span
        className="knob__cap"
        style={{ transform: `rotate(${-SWEEP / 2 + fraction * SWEEP}deg)` }}
      >
        <span className="knob__notch" />
      </span>
    </div>
  );
};

export default Knob;

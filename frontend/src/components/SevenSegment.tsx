import clsx from "clsx";

import type { ReadingTone } from "../utils/readouts";

import "./SevenSegment.scss";

/** Geometry of one digit cell, in the units of the glyph viewBox. */
const CELL_HEIGHT = 20;
const CELL_WIDTH = 12;
const DOT_WIDTH = 4;
const INSET = 1.6;
const MIDDLE = CELL_HEIGHT / 2;
const THICKNESS = 1.4;

/** A horizontal bar centred on `y`, drawn as a flattened hexagon. */
function horizontal(y: number): string {
  const left = INSET;
  const right = CELL_WIDTH - INSET;
  return [
    `${left},${y}`,
    `${left + THICKNESS},${y - THICKNESS}`,
    `${right - THICKNESS},${y - THICKNESS}`,
    `${right},${y}`,
    `${right - THICKNESS},${y + THICKNESS}`,
    `${left + THICKNESS},${y + THICKNESS}`,
  ].join(" ");
}

/** A vertical bar on `x`, running between `from` and `to`. */
function vertical(x: number, from: number, to: number): string {
  return [
    `${x},${from}`,
    `${x + THICKNESS},${from + THICKNESS}`,
    `${x + THICKNESS},${to - THICKNESS}`,
    `${x},${to}`,
    `${x - THICKNESS},${to - THICKNESS}`,
    `${x - THICKNESS},${from + THICKNESS}`,
  ].join(" ");
}

/** The seven bars, named a to g the way a display's datasheet names them. */
const SEGMENTS: Record<string, string> = {
  a: horizontal(INSET),
  b: vertical(CELL_WIDTH - INSET, INSET, MIDDLE),
  c: vertical(CELL_WIDTH - INSET, MIDDLE, CELL_HEIGHT - INSET),
  d: horizontal(CELL_HEIGHT - INSET),
  e: vertical(INSET, MIDDLE, CELL_HEIGHT - INSET),
  f: vertical(INSET, INSET, MIDDLE),
  g: horizontal(MIDDLE),
};

/**
 * Which bars each character lights.
 *
 * Only what seven bars can actually spell is here. Anything else falls back to
 * plain text, so a reading that is a word rather than a number still shows.
 */
const GLYPHS: Record<string, string> = {
  "-": "g",
  "—": "g",
  " ": "",
  0: "abcdef",
  1: "bc",
  2: "abdeg",
  3: "abcdg",
  4: "bcfg",
  5: "acdfg",
  6: "acdefg",
  7: "abc",
  8: "abcdefg",
  9: "abcdfg",
  A: "abcefg",
  b: "cdefg",
  C: "adef",
  c: "deg",
  d: "bcdeg",
  E: "adefg",
  F: "aefg",
  G: "acdef",
  H: "bcefg",
  h: "cefg",
  I: "ef",
  J: "bcd",
  L: "def",
  n: "ceg",
  O: "abcdef",
  o: "cdeg",
  P: "abefg",
  q: "abcfg",
  r: "eg",
  S: "acdfg",
  t: "defg",
  U: "bcdef",
  u: "cde",
  y: "bcdfg",
};

/**
 * The bars a character lights.
 *
 * The upper case form is tried first, because that is how a display spells a
 * word it can: "off" comes out as OFF, and "on" as On, the n having no upper
 * case form seven bars can make.
 */
function glyphOf(character: string): string | undefined {
  return GLYPHS[character.toUpperCase()] ?? GLYPHS[character] ?? GLYPHS[character.toLowerCase()];
}

/** One character and whether a decimal point rides on it. */
interface Cell {
  dot: boolean;
  glyph: string;
}

/** Split a reading into cells, or return null when a character has no glyph. */
function cellsOf(value: string): Cell[] | null {
  const cells: Cell[] = [];
  for (const character of value) {
    if (character === "." || character === ",") {
      if (cells.length > 0 && !cells[cells.length - 1].dot) {
        cells[cells.length - 1].dot = true;
        continue;
      }
    }
    const glyph = glyphOf(character);
    if (glyph === undefined) return null;
    cells.push({ dot: false, glyph });
  }
  return cells;
}

/** Props for {@link SevenSegment}. */
export interface SevenSegmentProps {
  /** Colour the digits burn. Which reading gets which is the caller's choice. */
  tone: ReadingTone;
  /** The reading to draw, already rounded and formatted. */
  value: string;
}

/**
 * A reading drawn the way a bench instrument's front panel draws it.
 *
 * Unlit bars stay faintly visible, as they are on a real display. The height
 * comes from whatever the surrounding rule sets, and the width follows from
 * how many characters there are.
 */
export const SevenSegment: React.FC<SevenSegmentProps> = ({ tone, value }) => {
  const cells = cellsOf(value);
  const className = clsx("seven-segment", `seven-segment--${tone}`);

  if (cells === null) {
    return <span className={className}>{value}</span>;
  }

  return (
    <span className={className}>
      <span className="seven-segment__text">{value}</span>
      {cells.map((cell, index) => (
        <svg
          aria-hidden="true"
          className="seven-segment__cell"
          key={index}
          viewBox={`0 0 ${cell.dot ? CELL_WIDTH + DOT_WIDTH : CELL_WIDTH} ${CELL_HEIGHT}`}
        >
          {Object.entries(SEGMENTS).map(([name, points]) => (
            <polygon
              className={clsx(
                "seven-segment__bar",
                cell.glyph.includes(name) && "seven-segment__bar--lit"
              )}
              key={name}
              points={points}
            />
          ))}
          {cell.dot && (
            <circle
              className="seven-segment__bar seven-segment__bar--lit"
              cx={CELL_WIDTH + DOT_WIDTH / 2}
              cy={CELL_HEIGHT - INSET}
              r={THICKNESS}
            />
          )}
        </svg>
      ))}
    </span>
  );
};

export default SevenSegment;

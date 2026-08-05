import clsx from "clsx";
import { Link } from "react-router";

import type { Instrument, InstrumentReadout } from "@api/types";
import SevenSegment from "@components/SevenSegment";
import { readingText, valueAt } from "../utils/readouts";

import "./InstrumentTile.scss";

/** How many readings fit on a tile before it stops being a glance. */
const MAX_READINGS = 4;

/**
 * Lamp colours the readings cycle through, in the order they are declared.
 *
 * The same rule the full panel uses, so an instrument burns the same colours
 * wherever it is drawn.
 */
const TONES = ["green", "red", "amber"] as const;

/** Props for {@link InstrumentTile}. */
export interface InstrumentTileProps {
  /** The instrument to draw, as the API reported it. */
  instrument: Instrument;
}

/**
 * Readings for an instrument that declared none, taken from its state.
 *
 * Scalars only, because a display can burn a number or a word and nothing
 * else. This is what keeps a provider that declares no readouts on the
 * dashboard rather than off it.
 */
function readoutsFromState(state: Record<string, unknown>): InstrumentReadout[] {
  return Object.entries(state)
    .filter(([, value]) => value === null || typeof value !== "object")
    .map(([key]) => ({
      group: "",
      key,
      label: key.replace(/_/g, " "),
      precision: null,
      role: "headline" as const,
      unit: "",
    }));
}

/** The readings the tile lights: the headline ones, in declared order. */
function tileReadouts(instrument: Instrument): InstrumentReadout[] {
  const declared = (instrument.readouts ?? []).filter((entry) => entry.role !== "summary");
  const readouts = declared.length > 0 ? declared : readoutsFromState(instrument.state);
  return readouts.slice(0, MAX_READINGS);
}

/**
 * One instrument as a display small enough to sit beside the host figures.
 *
 * It knows nothing about which instrument it is drawing: the readings come
 * from what the provider declared, or from its state when it declared
 * nothing. An instrument that is not answering shows why instead of a stale
 * reading.
 */
export const InstrumentTile: React.FC<InstrumentTileProps> = ({ instrument }) => {
  const readouts = tileReadouts(instrument);

  return (
    <Link
      className="instrument-tile"
      to="/instruments"
      aria-label={`${instrument.name}, ${instrument.available ? "available" : "unavailable"}`}
    >
      <div className="instrument-tile__head">
        <span
          className={clsx(
            "instrument-tile__dot",
            instrument.available && "instrument-tile__dot--on"
          )}
          aria-hidden="true"
        />
        <span className="instrument-tile__name">{instrument.name}</span>
        {instrument.kind !== instrument.name && (
          <span className="instrument-tile__kind">{instrument.kind}</span>
        )}
      </div>

      {instrument.available ? (
        <div className="instrument-tile__display">
          {readouts.map((readout, index) => (
            <div className="instrument-tile__reading" key={readout.key}>
              <SevenSegment
                tone={TONES[index % TONES.length]}
                value={readingText(valueAt(instrument.state, readout.key), readout.precision)}
              />
              {readout.unit && <span className="instrument-tile__unit">{readout.unit}</span>}
              <span className="instrument-tile__label">{readout.label}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="instrument-tile__unavailable">
          {instrument.unavailable_reason || "unavailable"}
        </p>
      )}
    </Link>
  );
};

export default InstrumentTile;

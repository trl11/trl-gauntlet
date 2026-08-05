import clsx from "clsx";
import { useEffect, useState } from "react";

import type { Instrument, InstrumentReadout } from "@api/types";
import CommandForm from "@components/CommandForm";
import InstrumentState from "@components/InstrumentState";
import ReadoutChart from "@components/ReadoutChart";
import SevenSegment from "@components/SevenSegment";
import { readingText, readoutGroups, valueAt, type ReadoutGroup } from "../utils/readouts";

import "./InstrumentPanel.scss";

/** How many polls of history the headline chart keeps. */
const MAX_SAMPLES = 60;

/**
 * Lamp colours the display cycles through, in the order readouts arrive.
 *
 * Which reading burns which colour is a matter of position alone, so a panel
 * for an instrument nobody has seen still lights up like a bench instrument.
 */
const TONES = ["green", "red", "amber"] as const;

/** Props every instrument panel takes. */
export interface InstrumentPanelProps {
  /** Disables every control while a command is in flight. */
  busy?: boolean;
  /** Message from the command that last failed. */
  error?: string | null;
  /** The instrument to draw, as the API reported it. */
  instrument: Instrument;
  /** Sends one command to this instrument. */
  onCommand: (command: string, args: Record<string, unknown>) => void | Promise<unknown>;
}

/**
 * One reading lit on the display, with its unit ringed the way a panel rings it.
 *
 * A reading that is on burns green whatever colour its position would give it,
 * because that is what an indicator lamp does.
 */
const Reading: React.FC<{
  readout: InstrumentReadout;
  state: Record<string, unknown>;
  tone: (typeof TONES)[number];
}> = ({ readout, state, tone }) => {
  const value = valueAt(state, readout.key);
  return (
    <div className="instrument-panel__reading">
      <SevenSegment
        tone={value === true ? "green" : tone}
        value={readingText(value, readout.precision)}
      />
      {readout.unit && <span className="instrument-panel__unit">{readout.unit}</span>}
      <span className="instrument-panel__reading-label">{readout.label}</span>
    </div>
  );
};

/** One group of readouts: the lit display, then its chart. */
const ReadoutGroupView: React.FC<{
  group: ReadoutGroup;
  history: Array<Record<string, number>>;
  state: Record<string, unknown>;
}> = ({ group, history, state }) => (
  <section className="instrument-panel__group">
    {group.name && <h3 className="instrument-panel__group-name">{group.name}</h3>}
    <div className="instrument-panel__display">
      {group.headline.length > 0 && (
        <div className="instrument-panel__readings">
          {group.headline.map((entry, index) => (
            <Reading
              key={entry.key}
              readout={entry}
              state={state}
              tone={TONES[index % TONES.length]}
            />
          ))}
        </div>
      )}
      {group.summary.length > 0 && (
        <div className="instrument-panel__readings instrument-panel__readings--small">
          {group.summary.map((entry) => (
            <Reading key={entry.key} readout={entry} state={state} tone="amber" />
          ))}
        </div>
      )}
    </div>
    <ReadoutChart
      history={history}
      series={group.headline
        .filter((entry) => typeof valueAt(state, entry.key) === "number")
        .map((entry) => ({ key: entry.key, label: entry.label }))}
    />
  </section>
);

/**
 * Draws any instrument from its reported state and its declared commands.
 *
 * A provider that declares `readouts` gets tiles, a rolling chart and a
 * compact strip laid out the way it asked for; one that declares none gets
 * every state value listed as a key and a value. Nothing here knows which
 * instrument it is looking at, so a provider that registers with Gauntlet gets
 * a working panel with no frontend change.
 */
export const InstrumentPanel: React.FC<InstrumentPanelProps> = ({
  busy = false,
  error,
  instrument,
  onCommand,
}) => {
  const [history, setHistory] = useState<Array<Record<string, number>>>([]);

  useEffect(() => {
    const sample: Record<string, number> = {};
    for (const entry of instrument.readouts ?? []) {
      const value = valueAt(instrument.state, entry.key);
      if (entry.role !== "summary" && typeof value === "number") sample[entry.key] = value;
    }
    if (Object.keys(sample).length === 0) return;
    setHistory((current) => [...current, sample].slice(-MAX_SAMPLES));
  }, [instrument]);

  const disabled = busy || !instrument.available;
  const groups = readoutGroups(instrument.readouts ?? []);
  const rest = instrument.commands.filter((command) => command.name !== instrument.primary_command);
  const primary = instrument.commands.find(
    (command) => command.name === instrument.primary_command
  );
  const footer = rest.filter((command) => command.fields.length === 0);
  const rows = rest.filter((command) => command.fields.length > 0);
  const subtitle = [instrument.instance_id, instrument.connection].filter(Boolean).join(" · ");

  return (
    <div className="instrument-panel">
      <header className="instrument-panel__head">
        <div className="instrument-panel__titles">
          <h2 className="instrument-panel__name">{instrument.name}</h2>
          <p className="instrument-panel__sub">{subtitle}</p>
        </div>
        <span
          aria-live="polite"
          className={clsx(
            "instrument-panel__chip",
            instrument.available && "instrument-panel__chip--on"
          )}
        >
          <span className="instrument-panel__dot" aria-hidden="true" />
          {instrument.available ? "AVAILABLE" : "UNAVAILABLE"}
        </span>
      </header>

      {instrument.description && (
        <p className="instrument-panel__description">{instrument.description}</p>
      )}

      {groups.length === 0 ? (
        <InstrumentState state={instrument.state} />
      ) : (
        groups.map((group) => (
          <ReadoutGroupView
            group={group}
            history={history}
            key={group.name}
            state={instrument.state}
          />
        ))
      )}

      {!instrument.available && (
        <p className="instrument-panel__unavailable">
          {instrument.unavailable_reason ||
            "the provider reports this instrument as unavailable; controls are read-only"}
        </p>
      )}

      {instrument.commands.length === 0 && (
        <p className="instrument-panel__quiet">Takes no commands.</p>
      )}

      <div className="instrument-panel__deck">
        {rows.length > 0 && (
          <div className="instrument-panel__modules">
            {rows.map((command) => (
              <CommandForm
                key={command.name}
                command={command}
                disabled={disabled}
                onSubmit={(args) => onCommand(command.name, args)}
              />
            ))}
          </div>
        )}

        {footer.length > 0 && (
          <div className="instrument-panel__keypad">
            {footer.map((command) => (
              <CommandForm
                key={command.name}
                command={command}
                disabled={disabled}
                onSubmit={(args) => onCommand(command.name, args)}
              />
            ))}
          </div>
        )}

        {primary && (
          <CommandForm
            command={primary}
            disabled={disabled}
            held={Boolean(instrument.in_use_by)}
            onSubmit={(args) => onCommand(primary.name, args)}
            primary
          />
        )}
      </div>

      {instrument.in_use_by && (
        <p className="instrument-panel__held">
          run {instrument.in_use_by} is driving this instrument; its key stays locked until the run
          ends
        </p>
      )}

      {error && (
        <p className="instrument-panel__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
};

export default InstrumentPanel;

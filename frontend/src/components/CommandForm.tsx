import { faLock, faLockOpen } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Button } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useId, useState } from "react";

import type { InstrumentCommand } from "@api/types";
import FieldControl from "@components/FieldControl";
import { coerce, fieldLabel, initialArgs, initialValue, latchField } from "../utils/commandFields";

import "./InstrumentPanel.scss";

/** Props {@link CommandForm} takes. */
export interface CommandFormProps {
  command: InstrumentCommand;
  disabled: boolean;
  /** A run is driving the instrument, so its latching key cannot be unlocked. */
  held?: boolean;
  onSubmit: (args: Record<string, unknown>) => void;
  /** Spans the panel and carries the emphasis, for the instrument's main action. */
  primary?: boolean;
  /** The instrument's current state, for a field whose choices come from it. */
  state?: Record<string, unknown>;
}

/**
 * What each row's controls start at: whatever the instrument is set to now.
 *
 * A row that says nothing about a field falls back to that field's own start,
 * so a provider need only declare the values it actually keeps.
 */
function initialRows(command: InstrumentCommand): Record<string, Record<string, unknown>> {
  const rows: Record<string, Record<string, unknown>> = {};
  for (const row of command.rows ?? []) {
    const values: Record<string, unknown> = {};
    for (const field of command.fields) {
      values[field.name] = row.values[field.name] ?? initialValue(field);
    }
    rows[row.key] = values;
  }
  return rows;
}

/** One declared command: its controls, then the key that sends them. */
const CommandForm: React.FC<CommandFormProps> = ({
  command,
  disabled,
  held = false,
  onSubmit,
  primary,
  state = {},
}) => {
  const fieldId = useId();
  const [args, setArgs] = useState<Record<string, unknown>>(() => initialArgs(command.fields));
  const [rows, setRows] = useState<Record<string, Record<string, unknown>>>(() =>
    initialRows(command)
  );
  const [locked, setLocked] = useState(true);

  // A command settling several things at once is a table, and never a latching
  // key: the key stands for one boolean, and there are as many here as rows.
  const rowwise = (command.rows ?? []).length > 0;
  const latch = primary && !rowwise ? latchField(command) : null;
  const latched = latch !== null && args[latch.name] === true;

  // The lock stays where the operator left it. A run taking the instrument is
  // the one thing that moves it, and it leaves the key locked behind it, so
  // releasing the lock is always a deliberate act.
  useEffect(() => {
    if (held) setLocked(true);
  }, [held]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (rowwise) {
      const payload: Record<string, Record<string, unknown>> = {};
      for (const row of command.rows ?? []) {
        const values: Record<string, unknown> = {};
        for (const field of command.fields) {
          values[field.name] = coerce(field, rows[row.key]?.[field.name]);
        }
        payload[row.key] = values;
      }
      onSubmit({ rows: payload });
      return;
    }
    // A latching key sends the opposite of what it last sent, so the press
    // itself is the setting; every other command sends what its controls say.
    const values = latch === null ? args : { ...args, [latch.name]: !latched };
    const payload: Record<string, unknown> = {};
    for (const field of command.fields) payload[field.name] = coerce(field, values[field.name]);
    onSubmit(payload);
    if (latch !== null) setArgs(values);
  };

  const set = (name: string, value: unknown) =>
    setArgs((current) => ({ ...current, [name]: value }));

  const setRow = (key: string, name: string, value: unknown) =>
    setRows((current) => ({ ...current, [key]: { ...current[key], [name]: value } }));

  // The latching key is the boolean's control, so it does not get one of its
  // own; whatever else the command takes still does.
  const settings = command.fields.filter((field) => field.name !== latch?.name);

  // The things settled run across and the fields run down, so eight channels
  // are two rows of controls rather than eight. It reads the way the display
  // above it does, channel by channel from the left.
  const table = (
    <div className="instrument-panel__rows">
      <table className="instrument-panel__rows-table">
        <thead>
          <tr>
            <th scope="col">{command.row_label ?? ""}</th>
            {(command.rows ?? []).map((row) => (
              <th key={row.key} scope="col">
                {row.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {command.fields.map((field) => (
            <tr key={field.name}>
              <th scope="row">{fieldLabel(field)}</th>
              {(command.rows ?? []).map((row) => (
                <td key={row.key}>
                  <FieldControl
                    disabled={disabled}
                    field={field}
                    id={`${fieldId}-${row.key}-${field.name}`}
                    name={`${row.label} ${fieldLabel(field)}`}
                    onChange={(next) => setRow(row.key, field.name, next)}
                    state={state}
                    value={rows[row.key]?.[field.name]}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  // Only a command that settles down to one latching key gets the bare
  // "power key" look; a primary command that still gathers fields — a read
  // that wants an address — is a group like any other and keeps its border.
  return (
    <form
      className={clsx(
        "instrument-panel__command",
        latch !== null && "instrument-panel__command--primary"
      )}
      onSubmit={submit}
    >
      {rowwise && table}

      {!rowwise && settings.length > 0 && (
        <div className="instrument-panel__controls">
          {settings.map((field) => (
            <span className="instrument-panel__control" key={field.name}>
              <span className="instrument-panel__control-label">{fieldLabel(field)}</span>
              <FieldControl
                disabled={disabled}
                field={field}
                id={`${fieldId}-${field.name}`}
                name={fieldLabel(field)}
                onChange={(next) => set(field.name, next)}
                state={state}
                value={args[field.name]}
              />
            </span>
          ))}
        </div>
      )}

      {latch === null ? (
        <Button
          className={clsx("instrument-panel__go", command.danger && "instrument-panel__go--danger")}
          color="transparent"
          disabled={disabled}
          size="small"
          type="submit"
        >
          {command.label || command.name}
        </Button>
      ) : (
        <div className="instrument-panel__latch">
          <Button
            aria-checked={locked || held}
            aria-label="Lock"
            className={clsx(
              "instrument-panel__lock",
              !(locked || held) && "instrument-panel__lock--open"
            )}
            color="transparent"
            disabled={disabled || held}
            onClick={() => setLocked((current) => !current)}
            role="switch"
            type="button"
          >
            <FontAwesomeIcon icon={locked || held ? faLock : faLockOpen} />
            {locked || held ? "Locked" : "Unlocked"}
          </Button>
          <Button
            aria-pressed={latched}
            className={clsx("instrument-panel__power", latched && "instrument-panel__power--on")}
            color="transparent"
            disabled={disabled || locked || held}
            type="submit"
          >
            {command.label || command.name}
            <span className="instrument-panel__lamp">
              <span className="instrument-panel__lamp-dot" aria-hidden="true" />
              {latched ? "ON" : "OFF"}
            </span>
          </Button>
        </div>
      )}
    </form>
  );
};

export default CommandForm;

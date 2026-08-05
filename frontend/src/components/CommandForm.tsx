import { faLock, faLockOpen } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Button, Input, Select } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useId, useState } from "react";

import type { InstrumentCommand, InstrumentField } from "@api/types";
import Knob from "@components/Knob";

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
}

/** The label to show for a field, with its unit when it declares one. */
function fieldLabel(field: InstrumentField): string {
  const name = field.label || field.name;
  return field.unit ? `${name} (${field.unit})` : name;
}

/** Is this a number the operator can dial in, rather than only type. */
function dialled(field: InstrumentField): boolean {
  const numeric = field.type === "integer" || field.type === "number";
  return numeric && field.min !== null && field.max !== null && field.max > field.min;
}

/**
 * How far one turn of the dial moves a field.
 *
 * Whatever the range, a power of ten is chosen that divides it into somewhere
 * between a hundred and a thousand settings, which is about as fine as a dial
 * can be driven by hand. An integer field never steps by less than one.
 */
function stepOf(field: InstrumentField): number {
  const range = (field.max ?? 0) - (field.min ?? 0);
  const step =
    range > 0 ? Number(Math.pow(10, Math.floor(Math.log10(range / 100))).toPrecision(1)) : 1;
  return field.type === "integer" ? Math.max(1, Math.round(step)) : step;
}

/**
 * The one boolean a latching key drives, for a command shaped to have one.
 *
 * A command that settles a single true-or-false, and otherwise only picks
 * which thing to settle it for, is what a front panel gives a key that stays
 * down. Anything else keeps its controls and its send key.
 */
function latchField(command: InstrumentCommand): InstrumentField | null {
  const booleans = command.fields.filter((field) => field.type === "boolean");
  const rest = command.fields.filter((field) => field.type !== "boolean");
  if (booleans.length !== 1) return null;
  if (rest.some((field) => field.choices.length === 0)) return null;
  return booleans[0];
}

/** The value a field starts at: its lowest setting, its first choice, or empty. */
function initialValue(field: InstrumentField): unknown {
  if (field.type === "boolean") return false;
  if (field.choices.length > 0) return field.choices[0];
  if (dialled(field)) return field.min;
  return "";
}

function initialArgs(command: InstrumentCommand): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const field of command.fields) args[field.name] = initialValue(field);
  return args;
}

/** Coerce the raw control value to the type the field declares. */
function coerce(field: InstrumentField, value: unknown): unknown {
  if (field.type === "boolean") return value === true;
  if (field.type === "integer" || field.type === "number") return Number(value);
  return String(value ?? "");
}

/** One declared command: its controls, then the key that sends them. */
const CommandForm: React.FC<CommandFormProps> = ({
  command,
  disabled,
  held = false,
  onSubmit,
  primary,
}) => {
  const fieldId = useId();
  const [args, setArgs] = useState<Record<string, unknown>>(() => initialArgs(command));
  const [locked, setLocked] = useState(true);

  const latch = primary ? latchField(command) : null;
  const latched = latch !== null && args[latch.name] === true;

  // The lock stays where the operator left it. A run taking the instrument is
  // the one thing that moves it, and it leaves the key locked behind it, so
  // releasing the lock is always a deliberate act.
  useEffect(() => {
    if (held) setLocked(true);
  }, [held]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
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

  const control = (field: InstrumentField) => {
    const id = `${fieldId}-${field.name}`;
    const value = args[field.name];

    if (field.type === "boolean") {
      const on = value === true;
      return (
        <Button
          aria-checked={on}
          aria-label={fieldLabel(field)}
          className={clsx("instrument-panel__toggle", on && "instrument-panel__toggle--on")}
          color="transparent"
          disabled={disabled}
          onClick={() => set(field.name, !on)}
          role="switch"
          type="button"
        >
          {on ? "ON" : "OFF"}
        </Button>
      );
    }

    if (field.choices.length > 0) {
      return (
        <Select
          aria-label={fieldLabel(field)}
          className="instrument-panel__choice"
          disabled={disabled}
          id={id}
          onChange={(event) => set(field.name, event.target.value)}
          options={field.choices.map((choice) => ({ value: choice, label: choice }))}
          value={String(value ?? "")}
        />
      );
    }

    const numeric = field.type === "integer" || field.type === "number";
    const entry = (
      <Input
        aria-label={fieldLabel(field)}
        disabled={disabled}
        id={id}
        max={field.max ?? undefined}
        min={field.min ?? undefined}
        onChange={(event) => set(field.name, event.target.value)}
        step={field.type === "integer" ? 1 : "any"}
        type={numeric ? "number" : "text"}
        value={String(value ?? "")}
      />
    );

    if (!dialled(field)) return <span className="instrument-panel__entry">{entry}</span>;

    const reading = Number(value);
    return (
      <span className="instrument-panel__dial">
        <Knob
          disabled={disabled}
          label={fieldLabel(field)}
          max={field.max ?? 0}
          min={field.min ?? 0}
          onChange={(next) => set(field.name, next)}
          step={stepOf(field)}
          value={Number.isFinite(reading) ? reading : (field.min ?? 0)}
        />
        <span className="instrument-panel__entry">{entry}</span>
      </span>
    );
  };

  // The latching key is the boolean's control, so it does not get one of its
  // own; whatever else the command takes still does.
  const settings = command.fields.filter((field) => field.name !== latch?.name);

  return (
    <form
      className={clsx("instrument-panel__command", primary && "instrument-panel__command--primary")}
      onSubmit={submit}
    >
      {settings.length > 0 && (
        <div className="instrument-panel__controls">
          {settings.map((field) => (
            <span className="instrument-panel__control" key={field.name}>
              <span className="instrument-panel__control-label">{fieldLabel(field)}</span>
              {control(field)}
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

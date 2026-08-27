import { Button, Input, Select } from "@trl11/components/ui";
import clsx from "clsx";

import type { InstrumentField } from "@api/types";
import Knob from "@components/Knob";
import { dialled, isHexField, runtimeChoices, stepOf } from "../utils/commandFields";

import "./InstrumentPanel.scss";

/** Props {@link FieldControl} takes. */
export interface FieldControlProps {
  disabled: boolean;
  field: InstrumentField;
  id: string;
  name: string;
  onChange: (next: unknown) => void;
  /**
   * Draw a `choices_from` field's runtime picks beside the entry.
   *
   * A group with several commands shows them once, below its shared toolbar,
   * rather than under each field they would otherwise repeat beside.
   */
  showPicks?: boolean;
  /** The instrument's current state, for a field whose choices come from it. */
  state: Record<string, unknown>;
  value: unknown;
}

/**
 * One declared field's control, wherever it is drawn.
 *
 * The value and where an edit goes are passed in rather than read from one
 * place, so a field laid out on its own, a field shared across a group of
 * commands, and the same field laid out as a column of a table are the same
 * control.
 */
const FieldControl: React.FC<FieldControlProps> = ({
  disabled,
  field,
  id,
  name,
  onChange,
  showPicks = true,
  state,
  value,
}) => {
  if (field.type === "boolean") {
    const on = value === true;
    return (
      <Button
        aria-checked={on}
        aria-label={name}
        className={clsx("instrument-panel__toggle", on && "instrument-panel__toggle--on")}
        color="transparent"
        disabled={disabled}
        onClick={() => onChange(!on)}
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
        aria-label={name}
        className="instrument-panel__choice"
        disabled={disabled}
        id={id}
        onChange={(event) => onChange(event.target.value)}
        options={field.choices.map((choice) => ({ value: choice, label: choice }))}
        value={String(value ?? "")}
      />
    );
  }

  const numeric = field.type === "integer" || field.type === "number";
  const hex = isHexField(field);
  // A hex field is typed and shown as bare hex text — its own numeric
  // stepping and range make no sense against letters, so it takes a plain
  // text entry rather than a spinner.
  const entry = (
    <Input
      aria-label={name}
      disabled={disabled}
      id={id}
      max={hex ? undefined : (field.max ?? undefined)}
      min={hex ? undefined : (field.min ?? undefined)}
      onChange={(event) => onChange(event.target.value)}
      step={field.type === "integer" ? 1 : "any"}
      type={numeric && !hex ? "number" : "text"}
      value={String(value ?? "")}
    />
  );

  // Values discovered at runtime — a scan's addresses — are offered as
  // quick picks beside the entry, so choosing one is a click and typing
  // one by hand still works exactly as it did before this existed. A field
  // that names a source but has nothing from it yet still gets the row, so
  // where a pick will land is visible before anything has been found.
  const found = runtimeChoices(field, state);
  let picks: React.ReactNode = null;
  if (field.choices_from && showPicks) {
    picks =
      found.length > 0 ? (
        <span className="instrument-panel__picks" role="group" aria-label={`Detected ${name}`}>
          {found.map((pick) => (
            <Button
              className={clsx(
                "instrument-panel__pick",
                String(value ?? "") === pick && "instrument-panel__pick--selected"
              )}
              color="transparent"
              disabled={disabled}
              key={pick}
              onClick={() => onChange(pick)}
              size="small"
              type="button"
            >
              {pick}
            </Button>
          ))}
        </span>
      ) : (
        <span className="instrument-panel__picks-empty">nothing detected yet</span>
      );
  }

  if (!dialled(field)) {
    if (picks === null) return <span className="instrument-panel__entry">{entry}</span>;
    return (
      <span className="instrument-panel__field-stack">
        <span className="instrument-panel__entry">{entry}</span>
        {picks}
      </span>
    );
  }

  const reading = Number(value);
  return (
    <span className="instrument-panel__dial">
      <Knob
        disabled={disabled}
        label={name}
        max={field.max ?? 0}
        min={field.min ?? 0}
        onChange={(next) => onChange(next)}
        step={stepOf(field)}
        value={Number.isFinite(reading) ? reading : (field.min ?? 0)}
      />
      <span className="instrument-panel__entry">{entry}</span>
      {picks}
    </span>
  );
};

export default FieldControl;

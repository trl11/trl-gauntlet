import { Button, Checkbox, Input, Select } from "@trl11/components/ui";
import clsx from "clsx";
import { useId, useState } from "react";

import type { InstrumentCommand, InstrumentField } from "@api/types";

import "./InstrumentPanel.scss";

/** Props {@link CommandForm} takes. */
export interface CommandFormProps {
  command: InstrumentCommand;
  disabled: boolean;
  onSubmit: (args: Record<string, unknown>) => void;
  /** Spans the panel and carries the emphasis, for the instrument's main action. */
  primary?: boolean;
}

/** The label to show for a field, with its unit when it declares one. */
function fieldLabel(field: InstrumentField): string {
  const name = field.label || field.name;
  return field.unit ? `${name} (${field.unit})` : name;
}

/** The value a field starts at: its first choice, false, or empty. */
function initialValue(field: InstrumentField): unknown {
  if (field.type === "boolean") return false;
  if (field.choices.length > 0) return field.choices[0];
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

/** One declared command: its fields inline, then the button that sends them. */
const CommandForm: React.FC<CommandFormProps> = ({ command, disabled, onSubmit, primary }) => {
  const fieldId = useId();
  const [args, setArgs] = useState<Record<string, unknown>>(() => initialArgs(command));

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const payload: Record<string, unknown> = {};
    for (const field of command.fields) payload[field.name] = coerce(field, args[field.name]);
    onSubmit(payload);
  };

  const set = (name: string, value: unknown) =>
    setArgs((current) => ({ ...current, [name]: value }));

  return (
    <form
      className={clsx("instrument-panel__command", primary && "instrument-panel__command--primary")}
      onSubmit={submit}
    >
      {command.fields.map((field) => {
        const id = `${fieldId}-${field.name}`;
        const value = args[field.name];
        if (field.type === "boolean") {
          return (
            <Checkbox
              key={field.name}
              id={id}
              label={fieldLabel(field)}
              checked={value === true}
              disabled={disabled}
              onChange={(event) => set(field.name, event.target.checked)}
            />
          );
        }
        if (field.choices.length > 0) {
          return (
            <Select
              key={field.name}
              id={id}
              aria-label={fieldLabel(field)}
              className="instrument-panel__choice"
              options={field.choices.map((choice) => ({ value: choice, label: choice }))}
              value={String(value ?? "")}
              disabled={disabled}
              onChange={(event) => set(field.name, event.target.value)}
            />
          );
        }
        const numeric = field.type === "integer" || field.type === "number";
        return (
          <span className="instrument-panel__entry" key={field.name}>
            {field.unit && <span className="instrument-panel__prefix">{field.unit}</span>}
            <Input
              id={id}
              aria-label={fieldLabel(field)}
              type={numeric ? "number" : "text"}
              min={field.min ?? undefined}
              max={field.max ?? undefined}
              step={field.type === "integer" ? 1 : "any"}
              value={String(value ?? "")}
              disabled={disabled}
              onChange={(event) => set(field.name, event.target.value)}
            />
          </span>
        );
      })}
      <Button
        className={clsx("instrument-panel__go", command.danger && "instrument-panel__go--danger")}
        color="transparent"
        size="small"
        type="submit"
        disabled={disabled}
      >
        {command.label || command.name}
      </Button>
    </form>
  );
};

export default CommandForm;

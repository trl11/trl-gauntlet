import { Button } from "@trl11/components/ui";
import clsx from "clsx";
import { useId, useState } from "react";

import type { InstrumentCommand, InstrumentField } from "@api/types";
import FieldControl from "@components/FieldControl";
import { coerce, fieldLabel, initialArgs, runtimeChoices } from "../utils/commandFields";

import "./InstrumentPanel.scss";

/** Props {@link CommandGroup} takes. */
export interface CommandGroupProps {
  /** Every command sharing this group's `group` key, in declared order. */
  commands: InstrumentCommand[];
  disabled: boolean;
  onSubmit: (command: InstrumentCommand, args: Record<string, unknown>) => void;
  /** The instrument's current state, for a field whose choices come from it. */
  state?: Record<string, unknown>;
}

/** Every field named across the group, each kept once, in first-seen order. */
function unionFields(commands: InstrumentCommand[]): InstrumentField[] {
  const seen = new Set<string>();
  const fields: InstrumentField[] = [];
  for (const command of commands) {
    for (const field of command.fields) {
      if (seen.has(field.name)) continue;
      seen.add(field.name);
      fields.push(field);
    }
  }
  return fields;
}

/**
 * Several commands that act on the same thing, sharing one set of controls.
 *
 * A provider marks commands with the same `group` when an operator would
 * never fill them in twice — a write and a read both naming the address of
 * whatever they are talking to. Rather than one bordered card per command,
 * each repeating the field the last one just took, this draws the fields
 * once and a key per command beneath them; a command sends only the fields
 * it declared, read out of the one set of controls.
 */
const CommandGroup: React.FC<CommandGroupProps> = ({
  commands,
  disabled,
  onSubmit,
  state = {},
}) => {
  const fieldId = useId();
  const fields = unionFields(commands);
  const [args, setArgs] = useState<Record<string, unknown>>(() => initialArgs(fields));

  const set = (name: string, value: unknown) =>
    setArgs((current) => ({ ...current, [name]: value }));

  const send = (command: InstrumentCommand) => {
    const payload: Record<string, unknown> = {};
    for (const field of command.fields) payload[field.name] = coerce(field, args[field.name]);
    onSubmit(command, payload);
  };

  // A field naming a runtime source is where a detect's findings land, so
  // its picks are drawn once below the toolbar rather than under the field
  // itself — the output of pressing one of the buttons above, not a control.
  const detectable = fields.filter((field) => field.choices_from);

  return (
    <div className="instrument-panel__command instrument-panel__command--group">
      <div className="instrument-panel__controls">
        {fields.map((field) => (
          <span className="instrument-panel__control" key={field.name}>
            <span className="instrument-panel__control-label">{fieldLabel(field)}</span>
            <FieldControl
              disabled={disabled}
              field={field}
              id={`${fieldId}-${field.name}`}
              name={fieldLabel(field)}
              onChange={(next) => set(field.name, next)}
              showPicks={false}
              state={state}
              value={args[field.name]}
            />
          </span>
        ))}
      </div>
      <div className="instrument-panel__group-actions">
        {commands.map((command) => (
          <Button
            className={clsx(
              "instrument-panel__go",
              command.danger && "instrument-panel__go--danger"
            )}
            color="transparent"
            disabled={disabled}
            key={command.name}
            onClick={() => send(command)}
            size="small"
            type="button"
          >
            {command.label || command.name}
          </Button>
        ))}
      </div>
      {detectable.length > 0 && (
        <div className="instrument-panel__group-found">
          {detectable.map((field) => {
            const found = runtimeChoices(field, state);
            return (
              <div className="instrument-panel__picks-row" key={field.name}>
                <span className="instrument-panel__picks-label">Detected {fieldLabel(field)}</span>
                {found.length > 0 ? (
                  <span
                    aria-label={`Detected ${fieldLabel(field)}`}
                    className="instrument-panel__picks"
                    role="group"
                  >
                    {found.map((pick) => (
                      <Button
                        className={clsx(
                          "instrument-panel__pick",
                          String(args[field.name] ?? "") === pick &&
                            "instrument-panel__pick--selected"
                        )}
                        color="transparent"
                        disabled={disabled}
                        key={pick}
                        onClick={() => set(field.name, pick)}
                        size="small"
                        type="button"
                      >
                        {pick}
                      </Button>
                    ))}
                  </span>
                ) : (
                  <span className="instrument-panel__picks-empty">nothing detected yet</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default CommandGroup;

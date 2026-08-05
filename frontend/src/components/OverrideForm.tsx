import { Input, Select } from "@trl11/components/ui";
import { useId } from "react";

import type { SuiteOverride } from "@api/types";
import FieldCheckbox from "@components/FieldCheckbox";
import { isNumeric, type OverrideValues } from "../utils/overrides";

import "./OverrideForm.scss";

/** The `step` a numeric control takes. Text controls have none. */
function stepFor(override: SuiteOverride): number | string | undefined {
  if (override.type === "integer") return 1;
  if (override.type === "number") return "any";
  return undefined;
}

/** Props for {@link OverrideForm}. */
export interface OverrideFormProps {
  disabled?: boolean;
  /** Validation messages to show, keyed by override name. */
  errors?: Record<string, string>;
  /** Called with the whole value map every time a control changes. */
  onChange: (next: OverrideValues) => void;
  /** The suite's declared per-run knobs, in manifest order. */
  overrides: SuiteOverride[];
  values: OverrideValues;
}

/**
 * One control per declared override.
 *
 * Everything rendered comes from the manifest, so any conforming suite gets a
 * form without code that knows the suite.
 */
export const OverrideForm: React.FC<OverrideFormProps> = ({
  disabled = false,
  errors = {},
  onChange,
  overrides,
  values,
}) => {
  const fieldId = useId();

  if (overrides.length === 0) return null;

  const update = (name: string, next: boolean | string) => {
    onChange({ ...values, [name]: next });
  };

  return (
    <div className="override-form">
      {overrides.map((override) => {
        const id = `${fieldId}-${override.name}`;
        const name = override.label || override.name;
        const label = override.unit ? `${name} (${override.unit})` : name;
        const hint = override.help;

        if (override.type === "boolean") {
          return (
            <FieldCheckbox
              key={override.name}
              id={id}
              label={label}
              hint={hint || undefined}
              checked={values[override.name] === true}
              disabled={disabled}
              onChange={(checked) => update(override.name, checked)}
            />
          );
        }

        if (override.choices.length > 0) {
          return (
            <Select
              key={override.name}
              id={id}
              label={label}
              hint={hint || undefined}
              error={errors[override.name]}
              options={[
                { value: "", label: "(suite default)" },
                ...override.choices.map((choice) => ({ value: choice, label: choice })),
              ]}
              value={String(values[override.name] ?? "")}
              disabled={disabled}
              onChange={(event) => update(override.name, event.target.value)}
            />
          );
        }

        return (
          <Input
            key={override.name}
            id={id}
            label={label}
            hint={hint || undefined}
            error={errors[override.name]}
            type={isNumeric(override) ? "number" : "text"}
            step={stepFor(override)}
            min={override.minimum ?? undefined}
            max={override.maximum ?? undefined}
            placeholder="(suite default)"
            value={String(values[override.name] ?? "")}
            disabled={disabled}
            onChange={(event) => update(override.name, event.target.value)}
          />
        );
      })}
    </div>
  );
};

export default OverrideForm;

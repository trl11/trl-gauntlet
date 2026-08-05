import { Checkbox } from "@trl11/components/ui";

import "./FieldCheckbox.scss";

/** Props for {@link FieldCheckbox}. */
export interface FieldCheckboxProps {
  checked: boolean;
  disabled: boolean;
  hint?: string;
  id: string;
  label: string;
  onChange: (checked: boolean) => void;
}

/**
 * A checkbox labelled above its box rather than beside it.
 *
 * The kit sets a checkbox's text alongside its box. In a grid of inputs that
 * leaves the cell reading as unlabelled where every other cell is labelled
 * above, and puts the box on a different line. Here the label is the same
 * element the inputs use and the box takes their height.
 */
const FieldCheckbox: React.FC<FieldCheckboxProps> = ({
  checked,
  disabled,
  hint,
  id,
  label,
  onChange,
}) => (
  <div className="field-checkbox">
    <label htmlFor={id} className="field-checkbox__label">
      {label}
    </label>
    <Checkbox
      id={id}
      hint={hint}
      checked={checked}
      disabled={disabled}
      onChange={(event) => onChange(event.target.checked)}
    />
  </div>
);

export default FieldCheckbox;

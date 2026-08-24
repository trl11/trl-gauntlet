import { Select } from "@trl11/components/ui";

import useFieldMessage from "@hooks/useFieldMessage";

/** Props for {@link FieldSelect}. The kit's own, unchanged. */
export type FieldSelectProps = React.ComponentProps<typeof Select>;

/**
 * The kit's `Select`, showing a supplied `error` without waiting for a blur.
 *
 * See {@link useFieldMessage} for why that is not the kit's own behaviour.
 */
const FieldSelect: React.FC<FieldSelectProps> = (props) => {
  useFieldMessage(props.id, props.error);
  return <Select {...props} />;
};

export default FieldSelect;

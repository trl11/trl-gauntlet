import { Input } from "@trl11/components/ui";

import useFieldMessage from "@hooks/useFieldMessage";

/** Props for {@link FieldInput}. The kit's own, unchanged. */
export type FieldInputProps = React.ComponentProps<typeof Input>;

/**
 * The kit's `Input`, showing a supplied `error` without waiting for a blur.
 *
 * See {@link useFieldMessage} for why that is not the kit's own behaviour.
 */
const FieldInput: React.FC<FieldInputProps> = (props) => {
  useFieldMessage(props.id, props.error);
  return <Input {...props} />;
};

export default FieldInput;

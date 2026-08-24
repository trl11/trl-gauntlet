import { Button, Modal } from "@trl11/components/ui";
import { useId, useState } from "react";

import FieldInput from "@components/FieldInput";

import "./RenameDialog.scss";

/** Serials the API accepts. */
const SERIAL_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$/;

/** Props for {@link RenameDialog}. */
export interface RenameDialogProps {
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onRename: (serial: string) => void;
  serial: string;
}

/** Collects a new serial and validates it before the request goes out. */
const RenameDialog: React.FC<RenameDialogProps> = ({ busy, error, onCancel, onRename, serial }) => {
  const fieldId = useId();
  const [value, setValue] = useState(serial);
  const trimmed = value.trim();
  const invalid = trimmed !== "" && !SERIAL_PATTERN.test(trimmed);

  return (
    <Modal title={`Rename ${serial}`} onClose={onCancel}>
      <form
        className="rename-dialog__dialog"
        onSubmit={(event) => {
          event.preventDefault();
          if (!invalid && trimmed !== "" && trimmed !== serial) onRename(trimmed);
        }}
      >
        <FieldInput
          id={`${fieldId}-serial`}
          label="New serial"
          value={value}
          disabled={busy}
          autoFocus
          hint="A letter or digit, then any of . _ / - up to 64 characters."
          error={invalid ? "That is not a valid serial." : undefined}
          onChange={(event) => setValue(event.target.value)}
        />
        {error && (
          <p className="rename-dialog__error" role="alert">
            {error}
          </p>
        )}
        <div className="rename-dialog__dialog-actions">
          <Button type="button" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            type="submit"
            color="blue"
            disabled={busy || invalid || trimmed === "" || trimmed === serial}
          >
            Rename
          </Button>
        </div>
      </form>
    </Modal>
  );
};

export default RenameDialog;

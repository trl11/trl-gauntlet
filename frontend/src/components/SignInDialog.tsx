import { useQuery } from "@tanstack/react-query";
import { Button, Modal } from "@trl11/components/ui";
import { useId, useState } from "react";

import { getRunProvenance } from "@api/client";
import FieldInput from "@components/FieldInput";
import { setSignIn, type SignIn } from "@hooks/useSignIn";

import "./SignInDialog.scss";

/** Props for {@link SignInDialog}. */
export interface SignInDialogProps {
  /** Who is signed in now, whose values the form starts from. */
  current: SignIn | null;
  onClose: () => void;
}

/**
 * Collects who is at the bench, where, and the test session they are in.
 *
 * Each field completes from what earlier runs recorded, so a session two
 * operators share is spelled the same by both and filters as one.
 */
const SignInDialog: React.FC<SignInDialogProps> = ({ current, onClose }) => {
  const fieldId = useId();
  const [name, setName] = useState(current?.name ?? "");
  const [location, setLocation] = useState(current?.location ?? "");
  const [session, setSession] = useState(current?.session ?? "");
  const known = useQuery({ queryKey: ["runs", "provenance"], queryFn: getRunProvenance });

  const next = { location: location.trim(), name: name.trim(), session: session.trim() };
  const complete = next.name !== "" && next.location !== "" && next.session !== "";

  const fields = [
    { key: "name", label: "Name", options: known.data?.operators, set: setName, value: name },
    {
      key: "location",
      label: "Location",
      options: known.data?.locations,
      set: setLocation,
      value: location,
    },
    {
      key: "session",
      label: "Test session",
      options: known.data?.sessions,
      set: setSession,
      value: session,
    },
  ];

  return (
    <Modal title="Sign in" onClose={onClose}>
      <form
        className="sign-in-dialog__form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!complete) return;
          setSignIn(next);
          onClose();
        }}
      >
        <p className="sign-in-dialog__note">
          Runs you start and notes you write record these, so they can be found by location and
          session later. Nothing else needs them.
        </p>
        {fields.map((field, index) => (
          <div key={field.key}>
            <FieldInput
              id={`${fieldId}-${field.key}`}
              label={field.label}
              list={`${fieldId}-${field.key}-known`}
              value={field.value}
              autoFocus={index === 0}
              maxLength={120}
              onChange={(event) => field.set(event.target.value)}
            />
            <datalist id={`${fieldId}-${field.key}-known`}>
              {(field.options ?? []).map((option) => (
                <option key={option} value={option} />
              ))}
            </datalist>
          </div>
        ))}
        <div className="sign-in-dialog__actions">
          {current && (
            <Button
              type="button"
              className="sign-in-dialog__sign-out"
              onClick={() => {
                setSignIn(null);
                onClose();
              }}
            >
              Sign out
            </Button>
          )}
          <Button type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" color="blue" disabled={!complete}>
            Sign in
          </Button>
        </div>
      </form>
    </Modal>
  );
};

export default SignInDialog;

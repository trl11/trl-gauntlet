import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Modal, Select, Spinner } from "@trl11/components/ui";
import { useId, useState } from "react";
import { useNavigate } from "react-router";

import { startRun } from "@api/client";
import type { Suite } from "@api/types";
import OverrideForm from "@components/OverrideForm";
import {
  initialOverrideValues,
  overrideArgv,
  overridePayload,
  validateOverrides,
  type OverrideValues,
} from "../utils/overrides";

import "./RunStartModal.scss";

/** Props for {@link RunStartModal}. */
export interface RunStartModalProps {
  /** Profile selected in the catalog, if the operator picked one. */
  initialProfile?: string | null;
  onClose: () => void;
  suite: Suite;
}

/**
 * Collects the inputs for one run and posts it.
 *
 * Which fields appear comes entirely from the manifest: the profile list, the
 * `supports` flags, and the declared overrides.
 */
export const RunStartModal: React.FC<RunStartModalProps> = ({ initialProfile, onClose, suite }) => {
  const fieldId = useId();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const profiles = suite.profiles_available ?? [];
  const [profile, setProfile] = useState(
    initialProfile && profiles.some((entry) => entry.name === initialProfile)
      ? initialProfile
      : (profiles[0]?.name ?? "")
  );
  const [target, setTarget] = useState("");
  const [unitSerial, setUnitSerial] = useState("");
  const [values, setValues] = useState<OverrideValues>(() =>
    initialOverrideValues(suite.overrides)
  );

  const errors = validateOverrides(suite.overrides, values);
  const invalid = Object.keys(errors).length > 0;

  const start = useMutation({
    mutationFn: () =>
      startRun({
        suite: suite.key,
        profile: profile || null,
        target: suite.supports.target ? target.trim() || null : null,
        unit_serial: suite.supports.unit_serial ? unitSerial.trim() || null : null,
        overrides: overridePayload(suite.overrides, values),
      }),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      onClose();
      navigate(`/runs/${run.run_id}`);
    },
  });

  const argv = overrideArgv(suite.overrides, values);

  return (
    <Modal title={`Run ${suite.title}`} onClose={onClose} className="run-start-modal">
      <form
        className="run-start-modal__body"
        onSubmit={(event) => {
          event.preventDefault();
          if (!invalid && !start.isPending) start.mutate();
        }}
      >
        {suite.description && <p className="run-start-modal__description">{suite.description}</p>}

        <div className="run-start-modal__fields">
          {profiles.length > 0 ? (
            <Select
              id={`${fieldId}-profile`}
              label="Profile"
              options={[
                { value: "", label: "(no profile)" },
                ...profiles.map((entry) => ({
                  value: entry.name,
                  label: entry.user_authored ? `${entry.name} (edited)` : entry.name,
                })),
              ]}
              value={profile}
              disabled={start.isPending}
              onChange={(event) => setProfile(event.target.value)}
            />
          ) : (
            <p className="run-start-modal__note">This suite offers no profiles.</p>
          )}

          {suite.supports.target && (
            <Input
              id={`${fieldId}-target`}
              label="Target"
              hint="Address of the unit under test"
              placeholder="(configured default)"
              value={target}
              disabled={start.isPending}
              onChange={(event) => setTarget(event.target.value)}
            />
          )}

          {suite.supports.unit_serial && (
            <Input
              id={`${fieldId}-serial`}
              label="Unit serial"
              hint="Recorded against the unit's history"
              placeholder="HC-001"
              value={unitSerial}
              disabled={start.isPending}
              onChange={(event) => setUnitSerial(event.target.value)}
            />
          )}
        </div>

        {suite.overrides.length > 0 && (
          <section className="run-start-modal__section" aria-label="Overrides">
            <h2 className="run-start-modal__heading">Overrides</h2>
            <OverrideForm
              disabled={start.isPending}
              errors={errors}
              onChange={setValues}
              overrides={suite.overrides}
              values={values}
            />
          </section>
        )}

        <section className="run-start-modal__section" aria-label="Summary">
          <h2 className="run-start-modal__heading">Summary</h2>
          <dl className="run-start-modal__summary">
            <dt>Suite</dt>
            <dd className="mono">{suite.key}</dd>
            <dt>Profile</dt>
            <dd className="mono">{profile || "(none)"}</dd>
            {suite.supports.target && (
              <>
                <dt>Target</dt>
                <dd className="mono">{target.trim() || "(configured default)"}</dd>
              </>
            )}
            {suite.supports.unit_serial && (
              <>
                <dt>Unit serial</dt>
                <dd className="mono">{unitSerial.trim() || "(none)"}</dd>
              </>
            )}
            <dt>Extra arguments</dt>
            <dd className="mono">{argv.length > 0 ? argv.join(" ") : "(none)"}</dd>
          </dl>
        </section>

        {start.isError && (
          <p className="run-start-modal__error" role="alert">
            {start.error.message}
          </p>
        )}

        <div className="run-start-modal__actions">
          <Button type="button" onClick={onClose} disabled={start.isPending}>
            Cancel
          </Button>
          <Button type="submit" color="blue" disabled={invalid || start.isPending}>
            {start.isPending ? <Spinner /> : "Start run"}
          </Button>
        </div>
      </form>
    </Modal>
  );
};

export default RunStartModal;

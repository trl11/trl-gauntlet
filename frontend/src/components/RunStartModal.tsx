import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Checkbox, Input, Modal, Select, Spinner } from "@trl11/components/ui";
import { useEffect, useId, useState } from "react";
import { useNavigate } from "react-router";

import { getProfile, listInstruments, listUnits, startRun } from "@api/client";
import type { Instrument, Suite } from "@api/types";
import OverrideForm from "@components/OverrideForm";
import {
  initialOverrideValues,
  overrideArgv,
  overridePayload,
  profileFields,
  validateOverrides,
  type OverrideValues,
} from "../utils/overrides";

import "./RunStartModal.scss";

/**
 * Is this instrument one the suite already drives?
 *
 * A requirement is an instance key where the bench binds roles and a bare
 * capability name where it does not, so an instrument matches either the whole
 * key or the capability half of it.
 */
function isRequired(instrument: Instrument, requires: string[]): boolean {
  const capability = instrument.name.split(".")[0];
  return requires.some((entry) => entry === instrument.name || entry === capability);
}

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
 * `supports` flags, and the declared overrides. What can be recorded comes
 * from the bench: every instrument registered on it, whatever it is.
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
  const [watched, setWatched] = useState<string[]>([]);

  // Everything the bench has, so the operator can record an instrument this
  // suite does not drive — the supply feeding the unit, the chamber it sits
  // in. What the suite requires is recorded whether or not it is asked for.
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: listInstruments,
  });
  const available = (instruments.data?.instruments ?? []).filter((entry) => entry.available);
  const optional = available.filter((entry) => !isRequired(entry, suite.requires));

  // The selected profile holds the values the run would use, so the override
  // controls are seeded from it and reseeded whenever the profile changes.
  const content = useQuery({
    queryKey: ["profile", suite.key, profile],
    queryFn: () => getProfile(suite.key, profile),
    enabled: profile !== "" && suite.overrides.length > 0,
  });
  const body = profile === "" ? "" : (content.data?.body ?? "");
  useEffect(() => {
    setValues(initialOverrideValues(suite.overrides, profileFields(body)));
  }, [body, suite.overrides]);

  // The units already tested, offered as completions for the serial. Most
  // recently seen first, because the unit on the bench is usually the one just
  // run. Typing a serial no unit has yet is still how a new one is recorded.
  const units = useQuery({
    queryKey: ["units"],
    queryFn: listUnits,
    enabled: suite.supports.unit_serial,
  });
  const known = [...(units.data?.units ?? [])].sort((a, b) =>
    (b.last_seen ?? "").localeCompare(a.last_seen ?? "")
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
        observe: watched,
      }),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      onClose();
      navigate(`/runs/${run.run_id}`);
    },
  });

  const argv = overrideArgv(suite.overrides, values);
  const recorded = [...suite.requires, ...watched];

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

        <section className="run-start-modal__section" aria-label="Common settings">
          <h2 className="run-start-modal__heading">Common settings</h2>
          <div className="run-start-modal__fields">
            {profiles.length > 0 ? (
              <Select
                id={`${fieldId}-profile`}
                label="Profile"
                options={[
                  { value: "", label: "(no profile)" },
                  ...profiles.map((entry) => ({
                    value: entry.name,
                    label: entry.user_authored ? `${entry.label} (edited)` : entry.label,
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
              <>
                <Input
                  id={`${fieldId}-serial`}
                  label="Unit serial"
                  hint={
                    known.length > 0
                      ? "Pick a unit already tested, or type a new serial"
                      : "Recorded against the unit's history"
                  }
                  list={`${fieldId}-serials`}
                  placeholder="HC-001"
                  value={unitSerial}
                  disabled={start.isPending}
                  onChange={(event) => setUnitSerial(event.target.value)}
                />
                <datalist id={`${fieldId}-serials`}>
                  {known.map((entry) => (
                    <option key={entry.serial} value={entry.serial} />
                  ))}
                </datalist>
              </>
            )}
          </div>
        </section>

        {suite.overrides.length > 0 && (
          <section className="run-start-modal__section" aria-label="Overrides">
            <h2 className="run-start-modal__heading">Overrides</h2>
            <OverrideForm
              disabled={start.isPending || content.isLoading}
              errors={errors}
              onChange={setValues}
              overrides={suite.overrides}
              values={values}
            />
          </section>
        )}

        {optional.length > 0 && (
          <section className="run-start-modal__section" aria-label="Recording">
            <h2 className="run-start-modal__heading">Recording</h2>
            <p className="run-start-modal__note">
              {suite.requires.length > 0
                ? `${suite.requires.join(", ")} ${suite.requires.length === 1 ? "is" : "are"} recorded because this suite drives ${suite.requires.length === 1 ? "it" : "them"}. Add anything else worth a reading.`
                : "Every instrument picked here is read for as long as the run lasts."}
            </p>
            <div className="run-start-modal__instruments">
              {optional.map((entry) => (
                <Checkbox
                  key={entry.name}
                  id={`${fieldId}-observe-${entry.name}`}
                  label={entry.name}
                  hint={entry.description || entry.kind}
                  checked={watched.includes(entry.name)}
                  disabled={start.isPending}
                  onChange={(event) =>
                    setWatched((current) =>
                      event.target.checked
                        ? [...current, entry.name]
                        : current.filter((name) => name !== entry.name)
                    )
                  }
                />
              ))}
            </div>
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
            <dt>Recording</dt>
            <dd className="mono">{recorded.length > 0 ? recorded.join(", ") : "(nothing)"}</dd>
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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Confirm, Input, Modal, Spinner } from "@trl11/components/ui";
import { useEffect, useId, useMemo, useState } from "react";
import { parse, stringify } from "yaml";

import {
  deleteProfile,
  diffProfile,
  duplicateProfile,
  getProfile,
  getProfileSchema,
  saveProfile,
} from "@api/client";
import SchemaForm from "@components/SchemaForm";

import "./ProfileEditor.scss";

/** The parsed profile, or why it could not be parsed. */
interface ParsedProfile {
  data: Record<string, unknown> | null;
  error: string | null;
}

function parseProfile(body: string): ParsedProfile {
  if (body.trim() === "") return { data: {}, error: null };
  try {
    const value: unknown = parse(body);
    if (value == null) return { data: {}, error: null };
    if (typeof value !== "object" || Array.isArray(value)) {
      return { data: null, error: "a profile must be a YAML mapping" };
    }
    return { data: value as Record<string, unknown>, error: null };
  } catch (cause) {
    return { data: null, error: (cause as Error).message };
  }
}

/** Styling class for one line of a unified diff. */
function diffLineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---")) return "profile-editor__diff-meta";
  if (line.startsWith("@@")) return "profile-editor__diff-hunk";
  if (line.startsWith("+")) return "profile-editor__diff-add";
  if (line.startsWith("-")) return "profile-editor__diff-del";
  return "profile-editor__diff-same";
}

/** Props for {@link ProfileEditor}. */
export interface ProfileEditorProps {
  /** Filename of the profile, as listed for the suite. */
  name: string;
  onClose: () => void;
  /** Called after a duplicate or a delete, with the profile to show next. */
  onProfileChanged?: (name: string | null) => void;
  suiteKey: string;
}

/**
 * Edit one profile as a generated form or as raw YAML.
 *
 * The YAML text is the single source of truth; the form reads and rewrites it,
 * so switching modes never loses an edit.
 */
export const ProfileEditor: React.FC<ProfileEditorProps> = ({
  name,
  onClose,
  onProfileChanged,
  suiteKey,
}) => {
  const fieldId = useId();
  const queryClient = useQueryClient();

  const [body, setBody] = useState("");
  const [mode, setMode] = useState<"form" | "yaml">("form");
  const [diff, setDiff] = useState<string | null>(null);
  const [copyName, setCopyName] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<"close" | "delete" | null>(null);

  const profile = useQuery({
    queryKey: ["profile", suiteKey, name],
    queryFn: () => getProfile(suiteKey, name),
  });
  const schema = useQuery({
    queryKey: ["profile-schema", suiteKey],
    queryFn: () => getProfileSchema(suiteKey),
    retry: false,
    staleTime: 300_000,
  });

  const loaded = profile.data?.body ?? "";
  useEffect(() => {
    setBody(loaded);
    setDiff(null);
  }, [loaded]);

  const parsed = useMemo(() => parseProfile(body), [body]);
  const dirty = profile.data != null && body !== loaded;
  // The form needs a schema to lay out and a mapping to fill it from. Fields
  // the form has no control for are marked in place, pointing at YAML mode.
  const formAvailable = schema.data != null && parsed.data != null;

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["suites"] });
    queryClient.invalidateQueries({ queryKey: ["profile", suiteKey] });
  };

  const save = useMutation({
    mutationFn: () => saveProfile(suiteKey, name, body),
    onSuccess: invalidate,
  });
  const showDiff = useMutation({
    mutationFn: () => diffProfile(suiteKey, name, body),
    onSuccess: (result) => setDiff(result.diff),
  });
  const duplicate = useMutation({
    mutationFn: (newName: string) => duplicateProfile(suiteKey, name, newName),
    onSuccess: (result) => {
      invalidate();
      setCopyName(null);
      onProfileChanged?.(result.name);
    },
  });
  const remove = useMutation({
    mutationFn: () => deleteProfile(suiteKey, name),
    onSuccess: () => {
      invalidate();
      onProfileChanged?.(null);
      onClose();
    },
  });

  const failure = [save.error, showDiff.error, duplicate.error, remove.error].find(Boolean);
  const busy = save.isPending || duplicate.isPending || remove.isPending;

  const requestClose = () => {
    if (dirty) setConfirming("close");
    else onClose();
  };

  return (
    <Modal title={`Profile ${name}`} onClose={requestClose} className="profile-editor">
      <div className="profile-editor__toolbar">
        <div className="profile-editor__modes" role="tablist" aria-label="Editor mode">
          <Button
            type="button"
            role="tab"
            size="small"
            color={mode === "form" ? "blue" : "outline"}
            aria-selected={mode === "form"}
            disabled={!formAvailable}
            onClick={() => setMode("form")}
          >
            Form
          </Button>
          <Button
            type="button"
            role="tab"
            size="small"
            color={mode === "yaml" ? "blue" : "outline"}
            aria-selected={mode === "yaml"}
            onClick={() => setMode("yaml")}
          >
            YAML
          </Button>
        </div>
        <Button color="blue" disabled={!dirty || busy} onClick={() => save.mutate()}>
          {save.isPending ? <Spinner /> : "Save"}
        </Button>
        <Button
          disabled={!profile.data || showDiff.isPending}
          onClick={() => (diff === null ? showDiff.mutate() : setDiff(null))}
        >
          {diff === null ? "Show diff" : "Hide diff"}
        </Button>
        <Button disabled={busy} onClick={() => setCopyName(`copy-of-${name}`)}>
          Duplicate
        </Button>
        <Button color="red" disabled={busy} onClick={() => setConfirming("delete")}>
          Delete
        </Button>
      </div>

      <div className="profile-editor__body">
        {profile.isLoading && <Spinner className="profile-editor__spinner" />}
        {profile.isError && (
          <p className="profile-editor__error" role="alert">
            {profile.error.message}
          </p>
        )}

        {copyName !== null && (
          <div className="profile-editor__copy">
            <Input
              id={`${fieldId}-copy`}
              label="New profile name"
              value={copyName}
              disabled={duplicate.isPending}
              onChange={(event) => setCopyName(event.target.value)}
            />
            <Button
              color="blue"
              disabled={copyName.trim() === "" || duplicate.isPending}
              onClick={() => duplicate.mutate(copyName.trim())}
            >
              Create copy
            </Button>
            <Button onClick={() => setCopyName(null)}>Cancel</Button>
          </div>
        )}

        {profile.data && mode === "form" && formAvailable && schema.data && parsed.data && (
          <SchemaForm
            disabled={busy}
            onChange={(next) => setBody(stringify(next))}
            schema={schema.data}
            value={parsed.data}
          />
        )}

        {profile.data && mode === "form" && !formAvailable && (
          <p className="profile-editor__note">
            {schema.isError
              ? "This suite publishes no profile schema. Edit the YAML directly."
              : "This profile is not a YAML mapping. Edit the YAML directly."}
          </p>
        )}

        {profile.data && mode === "yaml" && (
          <textarea
            className="profile-editor__yaml mono"
            aria-label="Profile YAML"
            spellCheck={false}
            rows={20}
            value={body}
            disabled={busy}
            onChange={(event) => setBody(event.target.value)}
          />
        )}

        {parsed.error && (
          <p className="profile-editor__error" role="alert">
            {parsed.error}
          </p>
        )}
        {failure && (
          <p className="profile-editor__error" role="alert">
            {failure.message}
          </p>
        )}

        {diff !== null && (
          <pre className="profile-editor__diff" aria-label="Unified diff">
            {diff.trim() === "" ? (
              <span className="profile-editor__diff-same">
                No changes against the file on disk.
              </span>
            ) : (
              diff.split("\n").map((line, index) => (
                <span key={index} className={diffLineClass(line)}>
                  {line || " "}
                </span>
              ))
            )}
          </pre>
        )}

        {profile.data && <p className="profile-editor__path mono">{profile.data.path}</p>}
      </div>

      {confirming === "close" && (
        <Confirm onConfirm={onClose} onDismiss={() => setConfirming(null)}>
          Discard unsaved changes to {name}?
        </Confirm>
      )}
      {confirming === "delete" && (
        <Confirm
          onConfirm={() => {
            setConfirming(null);
            remove.mutate();
          }}
          onDismiss={() => setConfirming(null)}
        >
          Delete profile {name}? This cannot be undone.
        </Confirm>
      )}
    </Modal>
  );
};

export default ProfileEditor;

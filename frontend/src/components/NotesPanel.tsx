import { faTrash } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Button, Confirm, Input, Spinner, Tooltip } from "@trl11/components/ui";
import clsx from "clsx";
import { useId, useState } from "react";

import type { Note } from "@api/types";
import { formatRelativeTime, formatTimestamp } from "../utils/format";

import "./NotesPanel.scss";

/** Props for {@link NotesPanel}. */
export interface NotesPanelProps {
  /** Disables the form and the delete buttons while a mutation is in flight. */
  busy?: boolean;
  className?: string;
  /** Notes to render, newest first as returned by the API. */
  notes: Note[];
  /** Called with the composed note. Clearing the form waits on it resolving. */
  onAdd: (body: string, author: string | null) => void | Promise<unknown>;
  /** Called with the id of the note the operator confirmed deleting. */
  onDelete: (noteId: number) => void | Promise<unknown>;
  /**
   * Draws the panel's own "Notes" heading. Turn it off where the caller heads
   * the section itself, which also leaves it to name the region.
   */
  titled?: boolean;
}

/** Read, add, and delete the notes attached to a run or a unit. */
export const NotesPanel: React.FC<NotesPanelProps> = ({
  busy = false,
  className,
  notes,
  onAdd,
  onDelete,
  titled = true,
}) => {
  const fieldId = useId();
  const [body, setBody] = useState("");
  const [author, setAuthor] = useState("");
  const [pendingDelete, setPendingDelete] = useState<number | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = body.trim();
    if (!text || busy) return;
    await onAdd(text, author.trim() || null);
    setBody("");
  };

  const confirmDelete = async () => {
    if (pendingDelete === null) return;
    const id = pendingDelete;
    setPendingDelete(null);
    await onDelete(id);
  };

  return (
    <section className={clsx("notes-panel", className)} aria-label={titled ? "Notes" : undefined}>
      {titled ? (
        <div className="notes-panel__head">
          <h2 className="notes-panel__title">Notes</h2>
          {busy && <Spinner className="notes-panel__spinner" />}
        </div>
      ) : (
        busy && <Spinner className="notes-panel__spinner" />
      )}

      <form className="notes-panel__form" onSubmit={submit}>
        <Input
          id={`${fieldId}-body`}
          label="Add a note"
          placeholder="What happened?"
          value={body}
          disabled={busy}
          maxLength={2000}
          onChange={(event) => setBody(event.target.value)}
        />
        <Input
          id={`${fieldId}-author`}
          label="Author"
          placeholder="Optional"
          value={author}
          disabled={busy}
          maxLength={120}
          onChange={(event) => setAuthor(event.target.value)}
        />
        <Button type="submit" color="blue" disabled={busy || body.trim() === ""}>
          Add note
        </Button>
      </form>

      {notes.length === 0 ? (
        <p className="notes-panel__empty">No notes yet.</p>
      ) : (
        <ul className="notes-panel__list">
          {notes.map((note) => (
            <li key={note.id} className="notes-panel__note">
              <div className="notes-panel__meta">
                <span className="notes-panel__author">{note.author || "anonymous"}</span>
                <Tooltip content={formatTimestamp(note.created_at)}>
                  <time className="notes-panel__time" dateTime={note.created_at}>
                    {formatRelativeTime(note.created_at)}
                  </time>
                </Tooltip>
                <Tooltip content="Delete note">
                  <Button
                    size="small"
                    square
                    color="transparent"
                    disabled={busy}
                    aria-label={`Delete note ${note.id}`}
                    onClick={() => setPendingDelete(note.id)}
                  >
                    <FontAwesomeIcon icon={faTrash} />
                  </Button>
                </Tooltip>
              </div>
              <p className="notes-panel__body">{note.body}</p>
            </li>
          ))}
        </ul>
      )}

      {pendingDelete !== null && (
        <Confirm onConfirm={confirmDelete} onDismiss={() => setPendingDelete(null)}>
          Delete this note? This cannot be undone.
        </Confirm>
      )}
    </section>
  );
};

export default NotesPanel;

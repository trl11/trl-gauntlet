"""Operator notes attached to runs and units.

One table serves both. A note names its subject by kind and id, so a run note
and a unit note differ only in ``subject_kind``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gauntlet.storage.columns import add_missing_columns

NOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    body         TEXT NOT NULL,
    author       TEXT,
    created_at   TEXT NOT NULL,
    location     TEXT,
    session      TEXT
);
CREATE INDEX IF NOT EXISTS notes_subject ON notes (subject_kind, subject_id);
"""

SUBJECT_RUN = "run"
SUBJECT_UNIT = "unit"

#: A run's notes, beside its artifacts, so they survive the index being rebuilt.
NOTES_NAME = "notes.md"

# Opens each note in `notes.md`. The heading after it is for a person; this is
# what is read back, so a note's author or session never has to be parsed out
# of prose.
_MARKER = "<!-- note "


@dataclass
class NoteRow:
    """One note against one subject."""

    id: int
    subject_kind: str
    subject_id: str
    body: str
    created_at: str
    author: str | None = None
    location: str | None = None
    session: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "body": self.body,
            "author": self.author,
            "location": self.location,
            "session": self.session,
            "created_at": self.created_at,
        }


class NotesIndex:
    """Thread-safe SQLite wrapper for the notes table."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(NOTES_SCHEMA)
        self._conn.commit()
        add_missing_columns(self._conn, "notes", {"location": "TEXT", "session": "TEXT"})
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add(
        self,
        subject_kind: str,
        subject_id: str,
        body: str,
        author: str | None = None,
        created_at: str | None = None,
        location: str | None = None,
        session: str | None = None,
    ) -> NoteRow:
        """Append a note and return it with its assigned id.

        A note written here is stamped now. One restored from elsewhere carries
        the time it was first written, which is the only thing that makes it
        readable beside the run it is about.
        """
        created_at = created_at or _utc_iso()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO notes (subject_kind, subject_id, body, author, created_at, location, session) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (subject_kind, subject_id, body, author, created_at, location, session),
            )
            self._conn.commit()
            note_id = int(cursor.lastrowid or 0)
        return NoteRow(
            id=note_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            body=body,
            author=author,
            created_at=created_at,
            location=location,
            session=session,
        )

    def count(self, subject_kind: str, subject_id: str) -> int:
        """How many notes one subject has."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total FROM notes WHERE subject_kind = ? AND subject_id = ?",
                (subject_kind, subject_id),
            ).fetchone()
        return int(row["total"])

    def counts(self, subject_kind: str) -> dict[str, int]:
        """Note counts for every subject of one kind, keyed by subject id."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT subject_id, COUNT(*) AS total FROM notes WHERE subject_kind = ? GROUP BY subject_id",
                (subject_kind,),
            ).fetchall()
        return {str(row["subject_id"]): int(row["total"]) for row in rows}

    def delete(self, note_id: int) -> bool:
        """Remove one note. False when no note had that id."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_subject(self, subject_kind: str, subject_id: str) -> int:
        """Remove every note against one subject and return how many went."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM notes WHERE subject_kind = ? AND subject_id = ?",
                (subject_kind, subject_id),
            )
            self._conn.commit()
            return cursor.rowcount

    def get(self, note_id: int) -> NoteRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _to_row(row) if row else None

    def list(self, subject_kind: str, subject_id: str) -> list[NoteRow]:
        """Notes against one subject, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notes WHERE subject_kind = ? AND subject_id = ? ORDER BY id DESC",
                (subject_kind, subject_id),
            ).fetchall()
        return [_to_row(row) for row in rows]

    def rename_subject(self, subject_kind: str, old_id: str, new_id: str) -> int:
        """Move every note of one subject onto a new subject id."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE notes SET subject_id = ? WHERE subject_kind = ? AND subject_id = ?",
                (new_id, subject_kind, old_id),
            )
            self._conn.commit()
            return cursor.rowcount


def _to_row(row: sqlite3.Row) -> NoteRow:
    return NoteRow(**dict(row))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_notes_file(run_dir: Path, run_id: str, notes: list[NoteRow]) -> None:
    """Write a run's notes into its directory as markdown, oldest first.

    A run left with no notes has the file removed rather than emptied, and a
    directory that has gone is left alone.
    """
    if not run_dir.is_dir():
        return
    path = run_dir / NOTES_NAME
    if not notes:
        path.unlink(missing_ok=True)
        return
    lines = [f"# Notes on {run_id}", ""]
    for note in sorted(notes, key=lambda note: note.id):
        meta = {
            "author": note.author,
            "created_at": note.created_at,
            "location": note.location,
            "session": note.session,
        }
        heading = " · ".join(filter(None, [note.created_at, note.author, note.location, note.session]))
        lines += [f"{_MARKER}{json.dumps(meta)} -->", f"### {heading}", "", note.body, ""]
    scratch = run_dir / f".{NOTES_NAME}.tmp"
    scratch.write_text("\n".join(lines))
    os.replace(scratch, path)


def read_notes_file(run_dir: Path) -> list[NoteRow]:
    """The notes a run's ``notes.md`` holds, oldest first, or none when it has no such file."""
    try:
        text = (run_dir / NOTES_NAME).read_text()
    except OSError:
        return []
    notes: list[NoteRow] = []
    meta: dict[str, Any] | None = None
    body: list[str] = []

    def finish() -> None:
        text = "\n".join(body).strip()
        if meta is not None and text:
            notes.append(
                NoteRow(
                    id=0,
                    subject_kind=SUBJECT_RUN,
                    subject_id=run_dir.name,
                    body=text,
                    created_at=str(meta.get("created_at") or _utc_iso()),
                    author=_text(meta.get("author")),
                    location=_text(meta.get("location")),
                    session=_text(meta.get("session")),
                )
            )

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(_MARKER) and line.endswith("-->"):
            finish()
            try:
                parsed = json.loads(line[len(_MARKER) : -len("-->")])
            except json.JSONDecodeError:
                parsed = {}
            meta = parsed if isinstance(parsed, dict) else {}
            body = []
            if index + 1 < len(lines) and lines[index + 1].startswith("### "):
                index += 1
        elif meta is not None:
            body.append(line)
        index += 1
    finish()
    return notes


def _text(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None

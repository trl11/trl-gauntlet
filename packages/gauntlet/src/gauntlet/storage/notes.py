"""Operator notes attached to runs and units.

One table serves both. A note names its subject by kind and id, so a run note
and a unit note differ only in ``subject_kind``.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    body         TEXT NOT NULL,
    author       TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_subject ON notes (subject_kind, subject_id);
"""

SUBJECT_RUN = "run"
SUBJECT_UNIT = "unit"


@dataclass
class NoteRow:
    """One note against one subject."""

    id: int
    subject_kind: str
    subject_id: str
    body: str
    created_at: str
    author: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "body": self.body,
            "author": self.author,
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
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add(self, subject_kind: str, subject_id: str, body: str, author: str | None = None) -> NoteRow:
        """Append a note and return it with its assigned id."""
        created_at = _utc_iso()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO notes (subject_kind, subject_id, body, author, created_at) VALUES (?, ?, ?, ?, ?)",
                (subject_kind, subject_id, body, author, created_at),
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

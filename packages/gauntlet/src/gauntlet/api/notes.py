"""Shared plumbing for the note endpoints on runs and units.

Notes are the same resource whichever subject carries them, so both routers
call through here rather than each growing its own copy.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict

from gauntlet.storage import NotesIndex


class NoteBody(BaseModel):
    """Request body for writing a note.

    ``author``, ``location`` and ``session`` are who wrote it, where, and in
    which test session, as the operator checked in.
    """

    model_config = ConfigDict(extra="forbid")

    body: str
    author: str | None = None
    location: str | None = None
    session: str | None = None


def add_note(request: Request, subject_kind: str, subject_id: str, payload: NoteBody) -> dict[str, Any]:
    """Append a note to one subject."""
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="`body` must not be empty")
    return (
        _notes(request)
        .add(
            subject_kind,
            subject_id,
            body,
            author=clean(payload.author),
            location=clean(payload.location),
            session=clean(payload.session),
        )
        .to_dict()
    )


def clean(value: str | None) -> str | None:
    """Trimmed text, or None when nothing is left."""
    return (value or "").strip() or None


def delete_note(request: Request, subject_kind: str, subject_id: str, note_id: int) -> dict[str, Any]:
    """Remove one note, provided it belongs to the named subject."""
    notes = _notes(request)
    note = notes.get(note_id)
    if note is None or note.subject_kind != subject_kind or note.subject_id != subject_id:
        raise HTTPException(status_code=404, detail=f"unknown note {note_id}")
    notes.delete(note_id)
    return {"id": str(note_id), "deleted": True}


def list_notes(request: Request, subject_kind: str, subject_id: str) -> dict[str, Any]:
    """Every note against one subject, newest first."""
    return {"notes": [note.to_dict() for note in _notes(request).list(subject_kind, subject_id)]}


def _notes(request: Request) -> NotesIndex:
    index: NotesIndex = request.app.state.notes_index
    return index

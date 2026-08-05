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
    """Request body for writing a note."""

    model_config = ConfigDict(extra="forbid")

    body: str
    author: str | None = None


def add_note(request: Request, subject_kind: str, subject_id: str, payload: NoteBody) -> dict[str, Any]:
    """Append a note to one subject."""
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="`body` must not be empty")
    author = (payload.author or "").strip() or None
    return _notes(request).add(subject_kind, subject_id, body, author).to_dict()


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

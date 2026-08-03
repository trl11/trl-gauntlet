"""Units under test: their run history, their counters, and operator notes.

A unit is derived from the runs that name it. Renaming rewrites those run rows
so the history follows the unit; forgetting a unit drops only its metadata and
notes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from gauntlet.api.notes import NoteBody, add_note, delete_note, list_notes
from gauntlet.storage import SUBJECT_UNIT, RunFilters, UnitConflict, UnitRow, UnitsIndex

router = APIRouter()


class RenameBody(BaseModel):
    """Request body for renaming a unit."""

    model_config = ConfigDict(extra="forbid")

    serial: str


@router.get("/units")
async def get_units(request: Request) -> dict[str, Any]:
    """Every unit any run has named, most recently seen first."""
    return {"units": [unit.to_dict() for unit in _index(request).list()]}


@router.get("/units/{serial}")
async def get_unit(request: Request, serial: str) -> dict[str, Any]:
    """One unit, with its notes."""
    payload = _unit_or_404(request, serial).to_dict()
    payload.update(list_notes(request, SUBJECT_UNIT, serial))
    return payload


@router.patch("/units/{serial}")
async def rename_unit(request: Request, serial: str, body: RenameBody) -> dict[str, Any]:
    """Rename a unit, rewriting every run row that names it."""
    new_serial = body.serial.strip()
    if not new_serial:
        raise HTTPException(status_code=422, detail="`serial` must not be empty")
    try:
        renamed = _index(request).rename(serial, new_serial)
    except UnitConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if renamed is None:
        raise HTTPException(status_code=404, detail=f"unknown unit {serial!r}")
    return renamed.to_dict()


@router.delete("/units/{serial}")
async def forget_unit(request: Request, serial: str) -> dict[str, Any]:
    """Forget a unit's metadata and notes. Its runs stay in history."""
    _unit_or_404(request, serial)
    _index(request).delete(serial)
    return {"serial": serial, "deleted": True}


@router.get("/units/{serial}/history")
async def get_unit_history(request: Request, serial: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Every run recorded against one unit, most recent first."""
    _unit_or_404(request, serial)
    index = request.app.state.runs_index
    filters = RunFilters(unit_serial=serial)
    rows = index.list(filters, limit=limit, offset=offset)
    return {"runs": [row.to_dict() for row in rows], "total": index.count(filters)}


@router.get("/units/{serial}/notes")
async def get_unit_notes(request: Request, serial: str) -> dict[str, Any]:
    """Notes against one unit."""
    _unit_or_404(request, serial)
    return list_notes(request, SUBJECT_UNIT, serial)


@router.post("/units/{serial}/notes", status_code=201)
async def post_unit_note(request: Request, serial: str, body: NoteBody) -> dict[str, Any]:
    """Attach a note to one unit."""
    _unit_or_404(request, serial)
    # Metadata keeps the unit addressable once its run rows have aged out.
    _index(request).touch(serial)
    return add_note(request, SUBJECT_UNIT, serial, body)


@router.delete("/units/{serial}/notes/{note_id}")
async def delete_unit_note(request: Request, serial: str, note_id: int) -> dict[str, Any]:
    """Remove one note from a unit."""
    _unit_or_404(request, serial)
    return delete_note(request, SUBJECT_UNIT, serial, note_id)


def _index(request: Request) -> UnitsIndex:
    index: UnitsIndex = request.app.state.units_index
    return index


def _unit_or_404(request: Request, serial: str) -> UnitRow:
    unit = _index(request).get(serial)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"unknown unit {serial!r}")
    return unit

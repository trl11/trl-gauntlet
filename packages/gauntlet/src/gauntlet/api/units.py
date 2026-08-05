"""Units under test: their run history, their counters, and operator notes.

A unit is derived from the runs that name it. Renaming rewrites those run rows
so the history follows the unit; forgetting a unit drops only its metadata and
notes, and the unit stays derivable from the runs it leaves behind. Deleting it
with `runs=true` takes those runs too, and nothing is left to derive.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from gauntlet.api.notes import NoteBody, add_note, delete_note, list_notes
from gauntlet.api.runs import remove_run_dir
from gauntlet.storage import SUBJECT_RUN, SUBJECT_UNIT, RunFilters, UnitConflict, UnitRow, UnitsIndex

router = APIRouter()


class RenameBody(BaseModel):
    """Request body for renaming a unit."""

    model_config = ConfigDict(extra="forbid")

    serial: str


@router.get("/units")
async def get_units(request: Request) -> dict[str, Any]:
    """Every unit any run has named, most recently seen first."""
    units = [unit.to_dict() for unit in _index(request).list()]
    return {"units": units, "total": len(units)}


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
async def forget_unit(request: Request, serial: str, runs: bool = False) -> dict[str, Any]:
    """Forget a unit's metadata and notes.

    Its runs stay in history unless `runs` is set, which deletes every run the
    unit names as well, leaving nothing the unit could be derived from again.
    Refused while one of those runs is still in flight.
    """
    _unit_or_404(request, serial)
    deleted_runs = _delete_unit_runs(request, serial) if runs else 0
    _index(request).delete(serial)
    return {"id": serial, "deleted": True, "deleted_runs": deleted_runs}


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


def _delete_unit_runs(request: Request, serial: str) -> int:
    """Delete every run one unit names, with its notes and its directory.

    Refuses the whole set if any of them is still in flight, so a unit is
    never left half deleted.
    """
    index = request.app.state.runs_index
    filters = RunFilters(unit_serial=serial)
    total = index.count(filters)
    rows = index.list(filters, limit=total) if total else []
    supervisor = request.app.state.supervisor
    for row in rows:
        handle = supervisor.get(row.run_id)
        if handle is not None and not handle.finished:
            raise HTTPException(status_code=409, detail=f"run {row.run_id} is still in flight")
    for row in rows:
        if index.delete(row.run_id) is None:
            continue
        request.app.state.notes_index.delete_subject(SUBJECT_RUN, row.run_id)
        remove_run_dir(request, row.run_dir)
    return len(rows)


def _index(request: Request) -> UnitsIndex:
    index: UnitsIndex = request.app.state.units_index
    return index


def _unit_or_404(request: Request, serial: str) -> UnitRow:
    unit = _index(request).get(serial)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"unknown unit {serial!r}")
    return unit

"""Starting, watching, and stopping runs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from gauntlet.api.notes import NoteBody, add_note, delete_note, list_notes
from gauntlet.catalog import campaigns_by_suite
from gauntlet.storage import SUBJECT_RUN, RunFilters, RunRow
from gauntlet.supervisor import Event, RunConflict, RunHandle, RunRejected, RunRequest
from gauntlet.transfer import TransferError, archive_name, export_run, import_run, read_export

router = APIRouter()

# How long to wait before emitting an SSE comment to keep the connection warm.
_HEARTBEAT_S = 20.0


class StartRunBody(BaseModel):
    """Request body for starting a run."""

    model_config = ConfigDict(extra="forbid")

    suite: str
    profile: str | None = None
    target: str | None = None
    unit_serial: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    profile_body: str | None = Field(
        default=None,
        description="Inline YAML to run instead of a saved profile, without persisting it.",
    )


@router.get("/runs")
async def list_runs(
    request: Request,
    suite: str | None = None,
    unit_serial: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    after: str | None = None,
    before: str | None = None,
    has_notes: bool = False,
    sort: str = "started_at",
    direction: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of run history.

    ``status`` may be repeated to accept several. ``after`` and ``before`` are
    inclusive bounds on ``started_at``, as a date or a full timestamp.
    ``has_notes`` keeps only the runs an operator has written a note against.
    ``total`` counts every run matching the filters, not just this page.
    """
    supervisor = request.app.state.supervisor
    index = request.app.state.runs_index
    filters = RunFilters(
        suite=suite,
        unit_serial=unit_serial,
        status=tuple(status or ()),
        after=after,
        before=before,
        has_notes=has_notes,
    )
    live = {h.run_id: h.to_dict() for h in supervisor.list_runs() if not h.finished}
    rows = index.list(filters, limit=limit, offset=offset, sort=sort, descending=direction != "asc")
    # A run is indexed as soon as it starts, so the in-flight handle stands in
    # for its row and carries the fresher status. Once the run has finished the
    # row wins, because that is what a rename or any later edit rewrites.
    owners = _campaign_owners(request)
    payloads = [_with_campaign(live.get(row.run_id, row.to_dict()), owners) for row in rows]
    return {"runs": with_note_counts(request, payloads), "total": index.count(filters)}


@router.post("/runs", status_code=201)
async def start_run(request: Request, body: StartRunBody) -> dict[str, Any]:
    """Start a run. Fails fast when the request cannot be honoured."""
    supervisor = request.app.state.supervisor
    try:
        handle = await supervisor.start(
            RunRequest(
                suite=body.suite,
                profile=body.profile,
                target=body.target or request.app.state.settings.default_target or None,
                unit_serial=body.unit_serial,
                overrides=body.overrides,
                profile_body=body.profile_body,
            )
        )
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.runs_index.upsert(to_row(handle))
    return handle.to_dict()


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    """One run, live or from history.

    An in-flight run is answered from its handle, which carries the fresher
    status and the argv it was spawned with. A finished run is answered from
    the index, which is what a rename or any later edit rewrites.
    """
    owners = _campaign_owners(request)
    handle = request.app.state.supervisor.get(run_id)
    if handle is not None and not handle.finished:
        return _with_notes(request, _with_campaign(handle.to_dict(), owners))
    row = request.app.state.runs_index.get(run_id)
    if row is not None:
        return _with_notes(request, _with_campaign(row.to_dict(), owners))
    if handle is not None:
        return _with_notes(request, _with_campaign(handle.to_dict(), owners))
    raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")


@router.delete("/runs/{run_id}")
async def delete_run(request: Request, run_id: str) -> dict[str, Any]:
    """Permanently remove a finished run: its row, its notes, and its directory.

    Irreversible, and refused while the run is still in flight.
    """
    handle = request.app.state.supervisor.get(run_id)
    if handle is not None and not handle.finished:
        raise HTTPException(status_code=409, detail="run is still in flight")
    index = request.app.state.runs_index
    row = index.delete(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    request.app.state.notes_index.delete_subject(SUBJECT_RUN, run_id)
    remove_run_dir(request, row.run_dir)
    return {"id": run_id, "deleted": True}


@router.get("/runs/{run_id}/export")
async def export_run_archive(request: Request, run_id: str) -> FileResponse:
    """One run as a single archive: its directory, its row, and its notes.

    Refused while the run is in flight, because the artifacts are still being
    written and the archive would be half a run.
    """
    handle = request.app.state.supervisor.get(run_id)
    if handle is not None and not handle.finished:
        raise HTTPException(status_code=409, detail="run is still in flight")
    row = request.app.state.runs_index.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    notes = request.app.state.notes_index.list(SUBJECT_RUN, run_id)
    directory = Path(tempfile.mkdtemp(prefix="gauntlet-export-"))
    name = archive_name(run_id)
    export_run(row, notes, directory / name)
    return FileResponse(
        directory / name,
        media_type="application/zip",
        filename=name,
        background=BackgroundTask(shutil.rmtree, directory, True),
    )


@router.post("/runs/import", status_code=201)
async def import_run_archive(request: Request, overwrite: bool = False) -> dict[str, Any]:
    """Take a run exported from another instance and index it here.

    The archive is the request body rather than a form field, which keeps
    multipart parsing out of the dependencies. A run id already known here is
    refused unless ``overwrite`` says to replace it.
    """
    directory = Path(tempfile.mkdtemp(prefix="gauntlet-import-"))
    archive = directory / "upload.zip"
    try:
        with archive.open("wb") as sink:
            async for chunk in request.stream():
                sink.write(chunk)
        try:
            export = read_export(archive)
        except TransferError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        index = request.app.state.runs_index
        handle = request.app.state.supervisor.get(export.run_id)
        if handle is not None and not handle.finished:
            raise HTTPException(status_code=409, detail="run is still in flight")
        if not overwrite and index.get(export.run_id) is not None:
            raise HTTPException(status_code=409, detail=f"run {export.run_id!r} is already here")
        try:
            row = import_run(archive, request.app.state.settings.runs_dir, index, request.app.state.notes_index)
        except TransferError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return _with_campaign(row.to_dict(), _campaign_owners(request))


@router.get("/runs/{run_id}/notes")
async def get_run_notes(request: Request, run_id: str) -> dict[str, Any]:
    """Notes against one run."""
    _run_or_404(request, run_id)
    return list_notes(request, SUBJECT_RUN, run_id)


@router.post("/runs/{run_id}/notes", status_code=201)
async def post_run_note(request: Request, run_id: str, body: NoteBody) -> dict[str, Any]:
    """Attach a note to one run."""
    _run_or_404(request, run_id)
    return add_note(request, SUBJECT_RUN, run_id, body)


@router.delete("/runs/{run_id}/notes/{note_id}")
async def delete_run_note(request: Request, run_id: str, note_id: int) -> dict[str, Any]:
    """Remove one note from a run."""
    _run_or_404(request, run_id)
    return delete_note(request, SUBJECT_RUN, run_id, note_id)


@router.post("/runs/{run_id}/stop")
async def stop_run(request: Request, run_id: str) -> dict[str, Any]:
    """Ask a run to finish early and still write a verdict."""
    stopped = await request.app.state.supervisor.stop(run_id)
    if not stopped:
        raise HTTPException(status_code=409, detail="run is not stoppable")
    return {"run_id": run_id, "status": "stopping"}


@router.post("/runs/{run_id}/abort")
async def abort_run(request: Request, run_id: str) -> dict[str, Any]:
    """Terminate a run without waiting for a verdict."""
    aborted = await request.app.state.supervisor.abort(run_id)
    if not aborted:
        raise HTTPException(status_code=409, detail="run is not abortable")
    return {"run_id": run_id, "status": "aborting"}


@router.get("/runs/{run_id}/events")
async def stream_events(request: Request, run_id: str, since: int = 0) -> StreamingResponse:
    """Server-sent events for one run.

    Replays events after ``since`` before streaming live ones. A reconnecting
    client passes its last ``seq``.
    """
    handle = request.app.state.supervisor.get(run_id)
    if handle is None or handle.bus is None:
        raise HTTPException(status_code=404, detail=f"no live event stream for run {run_id!r}")
    return StreamingResponse(
        _events(request, handle, since),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _events(request: Request, handle: RunHandle, since: int) -> AsyncIterator[str]:
    bus = handle.bus
    assert bus is not None
    queue, replay = await bus.subscribe(since)
    try:
        for replayed in replay:
            yield _frame(replayed.to_dict())
        # A bus that closed before this subscription will never deliver the
        # sentinel, so the replay is the whole stream.
        if bus.closed:
            yield _frame({"type": "end", "run_id": handle.run_id})
            return
        while True:
            if await request.is_disconnected():
                return
            try:
                event: Event | None = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is None:
                yield _frame({"type": "end", "run_id": handle.run_id})
                return
            yield _frame(event.to_dict())
    finally:
        bus.unsubscribe(queue)
        with contextlib.suppress(Exception):
            while not queue.empty():
                queue.get_nowait()


def _frame(payload: dict[str, Any]) -> str:
    return f"event: {payload.get('type', 'message')}\ndata: {json.dumps(payload)}\n\n"


def remove_run_dir(request: Request, run_dir: str) -> None:
    """Delete a run's directory, refusing to touch anything outside runs_dir."""
    runs_dir = request.app.state.settings.runs_dir.resolve()
    path = Path(run_dir).resolve()
    if path == runs_dir or runs_dir not in path.parents:
        return
    shutil.rmtree(path, ignore_errors=True)


def _run_or_404(request: Request, run_id: str) -> None:
    """Reject a run id no live run and no history row answers to."""
    if request.app.state.supervisor.get(run_id) is not None:
        return
    if request.app.state.runs_index.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")


def _campaign_owners(request: Request) -> dict[str, Any]:
    """Which campaign groups each suite, resolved once for a whole response."""
    return campaigns_by_suite(request.app.state.catalog(), request.app.state.campaigns())


def _with_campaign(payload: dict[str, Any], owners: dict[str, Any]) -> dict[str, Any]:
    """Name the campaign a run's suite belongs to, or null.

    Derived from the suite key at request time, never recorded on the run: it
    says which campaign groups that suite now, not that the campaign started
    the run.
    """
    owner = owners.get(str(payload.get("suite", "")))
    payload["campaign"] = None if owner is None else {"key": owner.key, "title": owner.manifest.title}
    return payload


def with_note_counts(request: Request, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tell each run in a listing how many notes it carries.

    One query for the whole page, so a list of any length costs the same as a
    single run. Read at request time like the campaign, because a note written
    or deleted after the row was stored still has to show.
    """
    counts = request.app.state.notes_index.counts(SUBJECT_RUN)
    for payload in payloads:
        payload["note_count"] = counts.get(str(payload["run_id"]), 0)
    return payloads


def _with_notes(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """The same count for one run."""
    payload["note_count"] = request.app.state.notes_index.count(SUBJECT_RUN, str(payload["run_id"]))
    return payload


def to_row(handle: RunHandle) -> RunRow:
    """Convert a live run handle into the row the index stores."""
    return RunRow(
        run_id=handle.run_id,
        suite=handle.suite,
        status=handle.status,
        started_at=handle.started_at,
        run_dir=handle.run_dir,
        ended_at=handle.ended_at,
        duration_s=handle.duration_s,
        verdict=handle.verdict,
        fail_reason=handle.fail_reason,
        profile=handle.profile,
        target=handle.target,
        unit_serial=handle.unit_serial,
    )

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
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.background import BackgroundTask

from gauntlet.api.artifacts import run_directory
from gauntlet.api.notes import NoteBody, add_note, clean, delete_note, list_notes
from gauntlet.catalog import campaigns_by_suite
from gauntlet.daq_recording import MAX_POINTS, recordings, window
from gauntlet.report import render_report, report_name
from gauntlet.storage import SUBJECT_RUN, RunFilters, RunRow, write_notes_file
from gauntlet.supervisor import Event, RunConflict, RunHandle, RunRejected, RunRequest
from gauntlet.supervisor.upsets import (
    MAX_STOP_AFTER,
    SUMMARY_NAME,
    UpsetMonitor,
    check_limits,
    check_stop_after,
    check_window,
)
from gauntlet.transfer import TransferError, archive_name, export_run, import_run, read_export

router = APIRouter()

# How long to wait before emitting an SSE comment to keep the connection warm.
_HEARTBEAT_S = 20.0


class UpsetLimits(BaseModel):
    """One channel's limits. A limit left out is not watched."""

    model_config = ConfigDict(extra="forbid")

    high: float | None = None
    low: float | None = None


class UpsetInstrumentBody(BaseModel):
    """The limits and capture window one streaming instrument starts a run with."""

    model_config = ConfigDict(extra="forbid")

    channels: dict[str, UpsetLimits] = Field(default_factory=dict)
    enabled: dict[str, bool] = Field(
        default_factory=dict, description="Channels to put in or out of the scan list before the run starts."
    )
    pre_s: float | None = None
    post_s: float | None = None


class UpsetStartBody(BaseModel):
    """The upset monitor's settings at the start of a run."""

    model_config = ConfigDict(extra="forbid")

    instruments: dict[str, UpsetInstrumentBody] = Field(default_factory=dict)
    stop_after: StrictInt | None = None


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
    observe: list[str] = Field(
        default_factory=list,
        description="Instruments to record for the run's duration, by instance key, "
        "beyond the ones its suite requires.",
    )
    upsets: UpsetStartBody | None = Field(
        default=None,
        description="Limits for the streaming instruments the run watches, in force from its first scan.",
    )
    operator: str | None = Field(default=None, description="Who started the run, as they checked in.")
    location: str | None = Field(default=None, description="Where the run was started, as the operator checked in.")
    session: str | None = Field(default=None, description="The test session the run belongs to.")


@router.get("/runs")
async def list_runs(
    request: Request,
    suite: str | None = None,
    unit_serial: str | None = None,
    status: Annotated[list[str] | None, Query()] = None,
    after: str | None = None,
    before: str | None = None,
    has_notes: bool = False,
    favorite: bool = False,
    location: str | None = None,
    session: str | None = None,
    q: str | None = None,
    sort: str = "started_at",
    direction: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of run history.

    ``status`` may be repeated to accept several. ``after`` and ``before`` are
    inclusive bounds on ``started_at``, as a date or a full timestamp.
    ``has_notes`` keeps only the runs an operator has written a note against,
    and ``favorite`` only the runs marked as one. ``location`` and ``session``
    keep the runs recorded at that location and in that test session. ``q``
    keeps the runs whose id, suite, profile, unit, target, status, failure
    reason, operator, location or session contains it.
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
        favorite=favorite,
        location=location,
        session=session,
        search=q.strip() if q else None,
    )
    live = {h.run_id: h.to_dict() for h in supervisor.list_runs() if not h.finished}
    rows = index.list(filters, limit=limit, offset=offset, sort=sort, descending=direction != "asc")
    # A run is indexed as soon as it starts, so the in-flight handle stands in
    # for its row and carries the fresher status. Once the run has finished the
    # row wins, because that is what a rename or any later edit rewrites.
    owners = _campaign_owners(request)
    payloads = [_with_campaign(live.get(row.run_id, row.to_dict()), owners) for row in rows]
    return {"runs": with_notes_and_favorites(request, payloads), "total": index.count(filters)}


@router.post("/runs", status_code=201)
async def start_run(request: Request, body: StartRunBody) -> dict[str, Any]:
    """Start a run. Fails fast when the request cannot be honoured."""
    supervisor = request.app.state.supervisor
    try:
        upsets = _start_upsets(body.upsets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        handle = await supervisor.start(
            RunRequest(
                suite=body.suite,
                profile=body.profile,
                target=body.target or request.app.state.settings.default_target or None,
                unit_serial=body.unit_serial,
                overrides=body.overrides,
                profile_body=body.profile_body,
                observe=body.observe,
                upsets=upsets,
                operator=clean(body.operator),
                location=clean(body.location),
                session=clean(body.session),
            )
        )
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.runs_index.upsert(to_row(handle))
    return handle.to_dict()


def _start_upsets(body: UpsetStartBody | None) -> dict[str, Any]:
    """The monitor's starting settings as the supervisor takes them, refused if they could never apply."""
    if body is None:
        return {}
    if body.stop_after is not None:
        check_stop_after(body.stop_after)
    instruments: dict[str, Any] = {}
    for key, given in body.instruments.items():
        check_window("pre_s", given.pre_s)
        check_window("post_s", given.post_s)
        channels = {channel: (limits.low, limits.high) for channel, limits in given.channels.items()}
        for bounds in channels.values():
            check_limits(bounds)
        instruments[key] = {
            "channels": channels,
            "enabled": dict(given.enabled),
            "post_s": given.post_s,
            "pre_s": given.pre_s,
        }
    return {"instruments": instruments, "stop_after": body.stop_after}


@router.get("/runs/provenance")
async def get_run_provenance(request: Request) -> dict[str, Any]:
    """Every operator, location and test session any run was recorded with.

    What the history and unit filters offer, so a value no run carries is never
    one of them.
    """
    values = request.app.state.runs_index.provenance()
    return {"operators": values["operator"], "locations": values["location"], "sessions": values["session"]}


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
        return _with_notes_and_favorite(request, _with_campaign(handle.to_dict(), owners))
    row = request.app.state.runs_index.get(run_id)
    if row is not None:
        return _with_notes_and_favorite(request, _with_campaign(row.to_dict(), owners))
    if handle is not None:
        return _with_notes_and_favorite(request, _with_campaign(handle.to_dict(), owners))
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


@router.put("/runs/{run_id}/favorite")
async def favorite_run(request: Request, run_id: str) -> dict[str, Any]:
    """Mark a run as a favorite."""
    return _set_favorite(request, run_id, True)


@router.delete("/runs/{run_id}/favorite")
async def unfavorite_run(request: Request, run_id: str) -> dict[str, Any]:
    """Stop marking a run as a favorite."""
    return _set_favorite(request, run_id, False)


def _set_favorite(request: Request, run_id: str, favorite: bool) -> dict[str, Any]:
    index = request.app.state.runs_index
    if index.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    index.set_favorite(run_id, favorite)
    return {"run_id": run_id, "favorite": favorite}


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


@router.get("/runs/{run_id}/report")
async def get_run_report(request: Request, run_id: str) -> HTMLResponse:
    """One run as a single self-contained HTML page, offered as a download.

    Refused while the run is in flight, for the same reason as an export.
    """
    handle = request.app.state.supervisor.get(run_id)
    if handle is not None and not handle.finished:
        raise HTTPException(status_code=409, detail="run is still in flight")
    row = request.app.state.runs_index.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    notes = request.app.state.notes_index.list(SUBJECT_RUN, run_id)
    owner = _campaign_owners(request).get(row.suite)
    campaign = None if owner is None else owner.manifest.title
    return HTMLResponse(
        render_report(row, notes, campaign),
        headers={"Content-Disposition": f'attachment; filename="{report_name(run_id)}"'},
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
    note = add_note(request, SUBJECT_RUN, run_id, body)
    _write_notes(request, run_id)
    return note


@router.delete("/runs/{run_id}/notes/{note_id}")
async def delete_run_note(request: Request, run_id: str, note_id: int) -> dict[str, Any]:
    """Remove one note from a run."""
    _run_or_404(request, run_id)
    deleted = delete_note(request, SUBJECT_RUN, run_id, note_id)
    _write_notes(request, run_id)
    return deleted


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


class UpsetThresholdsBody(BaseModel):
    """Replaces one instrument's limits, and optionally the run's ``stop_after``."""

    model_config = ConfigDict(extra="forbid")

    instrument: str
    channels: dict[str, UpsetLimits] = Field(default_factory=dict)
    pre_s: float | None = None
    post_s: float | None = None
    stop_after: StrictInt | None = None


@router.get("/runs/{run_id}/upsets")
async def get_upsets(request: Request, run_id: str) -> dict[str, Any]:
    """The thresholds and the upsets recorded so far, for a live or a finished run.

    Answers empty for a run that never watched anything. While the run is in
    flight the thresholds are the monitor's own, so a change shows at once
    rather than when the file is next written.
    """
    _run_or_404(request, run_id)
    body: dict[str, Any] = {"events": [], "stop_after": 0, "stopped_run": False, "thresholds": {}}
    with contextlib.suppress(OSError, ValueError):
        body.update(json.loads((run_directory(request, run_id) / SUMMARY_NAME).read_text(encoding="utf-8")))
    monitor = _live_monitor(request, run_id)
    body["instruments"] = monitor.followed() if monitor is not None else []
    if monitor is not None:
        body.update(monitor.settings())
    return body


@router.put("/runs/{run_id}/upsets/thresholds")
async def put_upset_thresholds(request: Request, run_id: str, body: UpsetThresholdsBody) -> dict[str, Any]:
    """Set the limits on one streaming instrument of a run in flight."""
    _run_or_404(request, run_id)
    monitor = _live_monitor(request, run_id)
    if monitor is None:
        raise HTTPException(status_code=409, detail="run is not in flight")
    if body.stop_after is not None and not 0 <= body.stop_after <= MAX_STOP_AFTER:
        raise HTTPException(status_code=422, detail=f"stop_after must be a whole number from 0 to {MAX_STOP_AFTER}")
    try:
        monitor.set_thresholds(
            body.instrument,
            {channel: (limits.low, limits.high) for channel, limits in body.channels.items()},
            post_s=body.post_s,
            pre_s=body.pre_s,
        )
        if body.stop_after is not None:
            monitor.set_stop_after(body.stop_after)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"instruments": monitor.followed(), **monitor.settings()}


@router.get("/runs/{run_id}/upsets/trace")
async def get_upset_trace(
    request: Request,
    run_id: str,
    instrument: str,
    since: int = 1,
    display_hz: float = Query(0.0, ge=0),
    tail_s: float = Query(0.0, ge=0, le=35),
) -> dict[str, Any]:
    """Scans a streaming instrument has produced from ``since`` on, for a run in flight.

    ``display_hz`` thins the answer to about that many scans a second, for a
    view that cannot draw the stream's own rate, and ``tail_s`` has a first
    request start from the last seconds. Recording is unaffected by either.
    """
    _run_or_404(request, run_id)
    monitor = _live_monitor(request, run_id)
    if monitor is None:
        raise HTTPException(status_code=409, detail="run is not in flight")
    streamed = await asyncio.to_thread(lambda: monitor.trace(instrument, since, display_hz=display_hz, tail_s=tail_s))
    if streamed is None:
        raise HTTPException(status_code=404, detail=f"run does not watch {instrument!r}")
    return {
        "channels": streamed.channels,
        "instrument": instrument,
        "next_seq": streamed.next_seq,
        "rate_hz": streamed.rate_hz,
        "scans": [[seq, wall, values] for seq, _, wall, values in streamed.scans],
    }


@router.get("/runs/{run_id}/daq")
async def get_daq_recording(request: Request, run_id: str) -> dict[str, Any]:
    """What a run recorded from its streaming instruments, at the rate they scanned.

    Times are seconds from ``origin``, the first scan any of them gave.
    """
    _run_or_404(request, run_id)
    return await asyncio.to_thread(recordings, run_directory(request, run_id))


@router.get("/runs/{run_id}/daq/data")
async def get_daq_window(
    request: Request,
    run_id: str,
    instrument: str,
    start: float = 0.0,
    end: float = 1e12,
    points: int = Query(2000, ge=1, le=MAX_POINTS),
) -> dict[str, Any]:
    """The scans of one instrument between two times, thinned to about ``points``.

    A window with no more scans than that is returned as it is; a larger one as
    an envelope of each bucket's lowest and highest reading, so a spike
    survives however far the view is zoomed out.
    """
    _run_or_404(request, run_id)
    found = await asyncio.to_thread(window, run_directory(request, run_id), instrument, start, end, points)
    if found is None:
        raise HTTPException(status_code=404, detail=f"run recorded nothing from {instrument!r}")
    return found


@router.get("/runs/{run_id}/upsets/{index}")
async def get_upset_capture(request: Request, run_id: str, index: int) -> FileResponse:
    """The captured window of one upset, as CSV.

    The file is the one the upset's own entry names, never a path from the
    request.
    """
    _run_or_404(request, run_id)
    run_dir = run_directory(request, run_id)
    try:
        events = json.loads((run_dir / SUMMARY_NAME).read_text(encoding="utf-8"))["events"]
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"run has no upset {index}") from exc
    named = next((event["file"] for event in events if event.get("index") == index), None)
    path = (run_dir / named).resolve() if named else None
    if path is None or not path.is_file() or run_dir not in path.parents:
        raise HTTPException(status_code=404, detail=f"run has no upset {index}")
    return FileResponse(path, media_type="text/csv")


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


def _write_notes(request: Request, run_id: str) -> None:
    """Rewrite one run's ``notes.md`` from the index, so its directory carries its notes."""
    handle = request.app.state.supervisor.get(run_id)
    row = request.app.state.runs_index.get(run_id)
    run_dir = handle.run_dir if handle is not None else row.run_dir if row is not None else None
    if run_dir:
        write_notes_file(Path(run_dir), run_id, request.app.state.notes_index.list(SUBJECT_RUN, run_id))


def _live_monitor(request: Request, run_id: str) -> UpsetMonitor | None:
    """The upset monitor of a run in flight, ``None`` for any other run."""
    handle = request.app.state.supervisor.get(run_id)
    return handle.upsets if handle is not None and not handle.finished else None


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


def with_notes_and_favorites(request: Request, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tell each run in a listing how many notes it carries and whether it is a favorite.

    One query for the whole page, so a list of any length costs the same as a
    single run. Read at request time like the campaign, because a note written
    or deleted after the row was stored still has to show.
    """
    counts = request.app.state.notes_index.counts(SUBJECT_RUN)
    favorites = request.app.state.runs_index.favorites()
    for payload in payloads:
        payload["note_count"] = counts.get(str(payload["run_id"]), 0)
        payload["favorite"] = payload["run_id"] in favorites
    return payloads


def _with_notes_and_favorite(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """The same count for one run, and whether it is a favorite."""
    payload["note_count"] = request.app.state.notes_index.count(SUBJECT_RUN, str(payload["run_id"]))
    payload["favorite"] = payload["run_id"] in request.app.state.runs_index.favorites()
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
        operator=handle.operator,
        location=handle.location,
        session=handle.session,
    )

"""Starting, watching, and stopping runs."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from gauntlet.api.notes import NoteBody, add_note, delete_note, list_notes
from gauntlet.storage import SUBJECT_RUN, RunFilters, RunRow
from gauntlet.supervisor import Event, RunConflict, RunHandle, RunRejected, RunRequest

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
    sort: str = "started_at",
    direction: str = "desc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of run history.

    ``status`` may be repeated to accept several. ``after`` and ``before`` are
    inclusive bounds on ``started_at``, as a date or a full timestamp. ``total``
    counts every run matching the filters, not just this page.
    """
    supervisor = request.app.state.supervisor
    index = request.app.state.runs_index
    filters = RunFilters(
        suite=suite,
        unit_serial=unit_serial,
        status=tuple(status or ()),
        after=after,
        before=before,
    )
    live = {h.run_id: h.to_dict() for h in supervisor.list_runs() if not h.finished}
    rows = index.list(filters, limit=limit, offset=offset, sort=sort, descending=direction != "asc")
    # A run is indexed as soon as it starts, so the in-flight handle stands in
    # for its row and carries the fresher status. Once the run has finished the
    # row wins, because that is what a rename or any later edit rewrites.
    return {"runs": [live.get(row.run_id, row.to_dict()) for row in rows], "total": index.count(filters)}


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
    request.app.state.runs_index.upsert(_to_row(handle))
    return handle.to_dict()


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> dict[str, Any]:
    """One run, live or from history.

    An in-flight run is answered from its handle, which carries the fresher
    status and the argv it was spawned with. A finished run is answered from
    the index, which is what a rename or any later edit rewrites.
    """
    handle = request.app.state.supervisor.get(run_id)
    if handle is not None and not handle.finished:
        return handle.to_dict()
    row = request.app.state.runs_index.get(run_id)
    if row is not None:
        return row.to_dict()
    if handle is not None:
        return handle.to_dict()
    raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")


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


def _run_or_404(request: Request, run_id: str) -> None:
    """Reject a run id no live run and no history row answers to."""
    if request.app.state.supervisor.get(run_id) is not None:
        return
    if request.app.state.runs_index.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")


def _to_row(handle: RunHandle) -> RunRow:
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

"""Reading the files a run produced.

Every path is resolved inside the run directory and rejected if it escapes.

One file is fetched through ``artifacts/{path}``, which serves text inline and
anything else as a download; there is no second endpoint per artifact. The
exception is ``metrics``, which parses JSONL and pages it rather than handing
back the file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

router = APIRouter()

_MAX_INLINE_BYTES = 2 * 1024 * 1024
_TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".log", ".md", ".txt", ".xml", ".yaml", ".yml"}


def _run_dir(request: Request, run_id: str) -> Path:
    supervisor = request.app.state.supervisor
    handle = supervisor.get(run_id)
    raw = handle.run_dir if handle is not None else None
    if not raw:
        row = request.app.state.runs_index.get(run_id)
        raw = row.run_dir if row is not None else None
    if not raw:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    path = Path(raw).resolve()
    if not path.is_dir():
        raise HTTPException(status_code=404, detail=f"run directory missing: {path}")
    return path


def _resolve(run_dir: Path, relative: str) -> Path:
    target = (run_dir / relative).resolve()
    try:
        target.relative_to(run_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes the run directory") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"no such artifact: {relative}")
    return target


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(request: Request, run_id: str) -> dict[str, Any]:
    """Every file in the run directory, with sizes."""
    run_dir = _run_dir(request, run_id)
    entries = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        entries.append(
            {
                "path": str(path.relative_to(run_dir)),
                "size": path.stat().st_size,
                "text": path.suffix in _TEXT_SUFFIXES,
            }
        )
    return {"run_id": run_id, "run_dir": str(run_dir), "artifacts": entries}


@router.get("/runs/{run_id}/artifacts/{relative:path}")
async def get_artifact(request: Request, run_id: str, relative: str) -> Any:
    """One artifact. Text is returned inline, anything else as a file."""
    path = _resolve(_run_dir(request, run_id), relative)
    if path.suffix in _TEXT_SUFFIXES and path.stat().st_size <= _MAX_INLINE_BYTES:
        return PlainTextResponse(path.read_text(errors="replace"))
    return FileResponse(path)


@router.get("/runs/{run_id}/metrics")
async def get_metrics(request: Request, run_id: str, limit: int = 5000) -> dict[str, Any]:
    """Parsed ``metrics.jsonl``, for charting a finished run.

    Live runs stream the same records over SSE.
    """
    path = _resolve(_run_dir(request, run_id), "metrics.jsonl")
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if len(records) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return {"run_id": run_id, "count": len(records), "records": records}

"""Moving one run from one Gauntlet instance to another.

A run travels as a zip holding its whole directory under ``run/`` and an
``export.json`` naming what the directory cannot give back: the index row, so a
run recorded as ``error`` without a ``verdict.json`` still arrives, and the
operator notes, which live in the database rather than beside the artifacts.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gauntlet import __version__
from gauntlet.storage import SUBJECT_RUN, NoteRow, NotesIndex, RunRow, RunsIndex

EXPORT_API_VERSION = 1

MANIFEST_NAME = "export.json"

_RUN_PREFIX = "run/"

#: Row fields that travel. ``run_dir`` does not: it is a path on the machine
#: that exported the run, and the importing instance writes its own.
_PORTABLE_COLUMNS = (
    "run_id",
    "suite",
    "status",
    "started_at",
    "ended_at",
    "duration_s",
    "verdict",
    "fail_reason",
    "profile",
    "target",
    "unit_serial",
)


class TransferError(RuntimeError):
    """The archive is not a run export this version can read."""


@dataclass(frozen=True)
class Export:
    """The header of one archive, read without unpacking it."""

    run_id: str
    suite: str
    exported_at: str
    gauntlet_version: str
    row: RunRow
    notes: list[NoteRow]


def archive_name(run_id: str) -> str:
    """What an exported run is called when it is offered as a download."""
    return f"{run_id}.gauntlet-run.zip"


def export_run(row: RunRow, notes: list[NoteRow], destination: Path) -> Path:
    """Write one run's archive to ``destination`` and return that path.

    A run whose directory has gone is still exported, as its row and notes
    alone.
    """
    manifest = {
        "apiVersion": EXPORT_API_VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gauntletVersion": __version__,
        "run": {column: getattr(row, column) for column in _PORTABLE_COLUMNS},
        "notes": [{"body": n.body, "author": n.author, "created_at": n.created_at} for n in notes],
    }
    run_dir = Path(row.run_dir) if row.run_dir else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
        if run_dir is not None and run_dir.is_dir():
            for path in sorted(run_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, _RUN_PREFIX + str(path.relative_to(run_dir).as_posix()))
    return destination


def read_export(archive: Path) -> Export:
    """The archive's header, so a caller can check the run id before unpacking."""
    try:
        with zipfile.ZipFile(archive) as opened:
            raw = opened.read(MANIFEST_NAME)
    except KeyError as exc:
        raise TransferError(f"not a run export: no {MANIFEST_NAME}") from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise TransferError(f"not a readable zip archive: {exc}") from exc

    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TransferError(f"{MANIFEST_NAME} is not JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise TransferError(f"{MANIFEST_NAME} is not an object")

    version = manifest.get("apiVersion")
    if version != EXPORT_API_VERSION:
        raise TransferError(f"unsupported export apiVersion {version!r}, expected {EXPORT_API_VERSION}")

    run = manifest.get("run")
    if not isinstance(run, dict) or not run.get("run_id") or not run.get("suite"):
        raise TransferError(f"{MANIFEST_NAME} names no run")

    return Export(
        run_id=_segment(str(run["run_id"]), "run_id"),
        suite=_segment(str(run["suite"]), "suite"),
        exported_at=str(manifest.get("exported_at") or ""),
        gauntlet_version=str(manifest.get("gauntletVersion") or ""),
        row=_row(run),
        notes=_notes(manifest.get("notes"), str(run["run_id"])),
    )


def import_run(archive: Path, runs_dir: Path, runs: RunsIndex, notes: NotesIndex) -> RunRow:
    """Unpack one archive into ``runs_dir`` and index what it carried.

    Replaces whatever the run id already names, artifacts included, so the
    caller decides whether a collision is allowed before calling this.
    """
    export = read_export(archive)
    run_dir = runs_dir / export.suite / export.run_id
    _unpack(archive, run_dir)

    row = export.row
    row.run_dir = str(run_dir)
    runs.upsert(row)
    notes.delete_subject(SUBJECT_RUN, export.run_id)
    for note in reversed(export.notes):
        notes.add(SUBJECT_RUN, export.run_id, note.body, note.author, created_at=note.created_at)
    return row


def _unpack(archive: Path, run_dir: Path) -> None:
    """Extract the ``run/`` half of an archive, refusing any escaping member.

    Every member is resolved before anything is written, so an archive that
    would escape the run directory does not first empty it. What lands is the
    archive rather than the archive over the top of an earlier run.
    """
    root = run_dir.resolve()
    with zipfile.ZipFile(archive) as opened:
        members = [info for info in opened.infolist() if not info.is_dir() and info.filename.startswith(_RUN_PREFIX)]
        targets = []
        for info in members:
            target = (root / info.filename[len(_RUN_PREFIX) :]).resolve()
            if target != root and root not in target.parents:
                raise TransferError(f"archive member escapes the run directory: {info.filename}")
            targets.append(target)

        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        for info, target in zip(members, targets, strict=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            with opened.open(info) as source, target.open("wb") as sink:
                while chunk := source.read(1 << 20):
                    sink.write(chunk)


def _row(run: dict[str, Any]) -> RunRow:
    """The exported row, with its run directory left for the importer to set."""
    fields = {column: run.get(column) for column in _PORTABLE_COLUMNS}
    return RunRow(
        run_id=str(fields["run_id"]),
        suite=str(fields["suite"]),
        status=str(fields["status"] or "error"),
        started_at=str(fields["started_at"] or ""),
        run_dir="",
        ended_at=_text(fields["ended_at"]),
        duration_s=float(fields["duration_s"]) if isinstance(fields["duration_s"], (int, float)) else None,
        verdict=_text(fields["verdict"]),
        fail_reason=_text(fields["fail_reason"]),
        profile=_text(fields["profile"]),
        target=_text(fields["target"]),
        unit_serial=_text(fields["unit_serial"]),
    )


def _segment(value: str, field: str) -> str:
    """One path segment.

    Both the suite and the run id become directory names under the runs
    directory, so an archive naming anything else would write outside it.
    """
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise TransferError(f"{field} is not a usable directory name: {value!r}")
    return value


def _notes(raw: Any, run_id: str) -> list[NoteRow]:
    if not isinstance(raw, list):
        return []
    return [
        NoteRow(
            id=0,
            subject_kind=SUBJECT_RUN,
            subject_id=run_id,
            body=str(entry.get("body") or ""),
            created_at=str(entry.get("created_at") or ""),
            author=_text(entry.get("author")),
        )
        for entry in raw
        if isinstance(entry, dict) and entry.get("body")
    ]


def _text(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None

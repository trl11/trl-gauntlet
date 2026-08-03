"""Units under test, derived from the runs index.

A unit is not a record an operator creates. It exists because runs name it, and
its counters are an aggregate over the ``runs`` table. The ``units`` table holds
only the metadata that has to outlive those rows, so forgetting a unit never
loses run history.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gauntlet.storage.notes import SUBJECT_UNIT, NotesIndex
from gauntlet.storage.runs_index import RUNS_SCHEMA

UNITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    serial     TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_COUNTERS = """
SELECT unit_serial AS serial,
       COUNT(*) AS run_count,
       SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) AS passed,
       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
       MIN(started_at) AS first_seen,
       MAX(started_at) AS last_seen
FROM runs
WHERE unit_serial IS NOT NULL AND unit_serial <> ''
{where}
GROUP BY unit_serial
"""

# Oldest first, so the last row read for a serial is that unit's latest run.
_RUNS = """
SELECT unit_serial AS serial, run_id, suite, status, ended_at
FROM runs
WHERE unit_serial IS NOT NULL AND unit_serial <> ''
{where}
ORDER BY started_at
"""


class UnitConflict(RuntimeError):
    """The requested serial is already taken."""


@dataclass
class UnitRow:
    """One unit, with its run counters and latest run."""

    serial: str
    run_count: int = 0
    passed: int = 0
    failed: int = 0
    note_count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    last_run: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial": self.serial,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "run_count": self.run_count,
            "passed": self.passed,
            "failed": self.failed,
            "last_run": dict(self.last_run) if self.last_run else None,
            "note_count": self.note_count,
        }


class UnitsIndex:
    """Aggregates the runs table into units and owns their metadata."""

    def __init__(self, path: Path, notes: NotesIndex) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(RUNS_SCHEMA + UNITS_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()
        self._notes = notes

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def delete(self, serial: str) -> bool:
        """Forget a unit's metadata and notes, leaving its run rows in place."""
        self._notes.delete_subject(SUBJECT_UNIT, serial)
        with self._lock:
            cursor = self._conn.execute("DELETE FROM units WHERE serial = ?", (serial,))
            self._conn.commit()
            return cursor.rowcount > 0

    def get(self, serial: str) -> UnitRow | None:
        """One unit, or None when no run and no metadata names that serial."""
        with self._lock:
            counters = self._conn.execute(_COUNTERS.format(where="AND unit_serial = ?"), (serial,)).fetchone()
            runs = self._conn.execute(_RUNS.format(where="AND unit_serial = ?"), (serial,)).fetchall()
            metadata = self._conn.execute("SELECT * FROM units WHERE serial = ?", (serial,)).fetchone()
        if counters is None and metadata is None:
            return None
        row = _to_row(counters) if counters is not None else _bare_row(metadata)
        if runs:
            row.last_run = _last_run(runs[-1])
        row.note_count = self._notes.count(SUBJECT_UNIT, serial)
        return row

    def list(self) -> list[UnitRow]:
        """Every known unit, most recently seen first."""
        with self._lock:
            counters = self._conn.execute(_COUNTERS.format(where="")).fetchall()
            runs = self._conn.execute(_RUNS.format(where="")).fetchall()
            metadata = self._conn.execute("SELECT * FROM units").fetchall()
        rows = {str(row["serial"]): _to_row(row) for row in counters}
        for record in runs:
            rows[str(record["serial"])].last_run = _last_run(record)
        for record in metadata:
            serial = str(record["serial"])
            if serial not in rows:
                rows[serial] = _bare_row(record)
        counts = self._notes.counts(SUBJECT_UNIT)
        for serial, row in rows.items():
            row.note_count = counts.get(serial, 0)
        return sorted(rows.values(), key=lambda row: (row.last_seen or "", row.serial), reverse=True)

    def rename(self, serial: str, new_serial: str) -> UnitRow | None:
        """Move a unit, its notes, and every run that names it onto a new serial.

        Returns None when the old serial is unknown, and raises
        :class:`UnitConflict` when the new one is already in use.
        """
        if self.get(serial) is None:
            return None
        if new_serial != serial and self.get(new_serial) is not None:
            raise UnitConflict(f"unit {new_serial!r} already exists")
        if new_serial == serial:
            return self.get(serial)
        now = _utc_iso()
        with self._lock:
            self._conn.execute("UPDATE runs SET unit_serial = ? WHERE unit_serial = ?", (new_serial, serial))
            self._conn.execute("DELETE FROM units WHERE serial = ?", (serial,))
            self._conn.execute(
                "INSERT OR REPLACE INTO units (serial, created_at, updated_at) VALUES (?, ?, ?)",
                (new_serial, now, now),
            )
            self._conn.commit()
        self._notes.rename_subject(SUBJECT_UNIT, serial, new_serial)
        return self.get(new_serial)

    def touch(self, serial: str) -> None:
        """Record metadata for a serial so it survives losing its run rows."""
        now = _utc_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO units (serial, created_at, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(serial) DO UPDATE SET updated_at = excluded.updated_at",
                (serial, now, now),
            )
            self._conn.commit()


def _bare_row(record: sqlite3.Row) -> UnitRow:
    """A unit known only from its metadata, with no runs behind it."""
    created = str(record["created_at"])
    return UnitRow(serial=str(record["serial"]), first_seen=created, last_seen=created)


def _last_run(record: sqlite3.Row) -> dict[str, Any]:
    """The summary of one run as it appears on a unit."""
    return {
        "run_id": str(record["run_id"]),
        "suite": str(record["suite"]),
        "status": str(record["status"]),
        "ended_at": record["ended_at"],
    }


def _to_row(row: sqlite3.Row) -> UnitRow:
    return UnitRow(
        serial=str(row["serial"]),
        run_count=int(row["run_count"]),
        passed=int(row["passed"]),
        failed=int(row["failed"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
    )


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

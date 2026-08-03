"""SQLite index of past runs.

Run artifacts on disk are the source of truth. :meth:`RunsIndex.import_tree`
rebuilds this index from them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    suite       TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    duration_s  REAL,
    verdict     TEXT,
    fail_reason TEXT,
    profile     TEXT,
    target      TEXT,
    unit_serial TEXT,
    run_dir     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_suite_started ON runs (suite, started_at DESC);
CREATE INDEX IF NOT EXISTS runs_unit ON runs (unit_serial);
"""

_COLUMNS = (
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
    "run_dir",
)

#: Columns :meth:`RunsIndex.list` will sort by. Anything else falls back to
#: ``started_at``, so caller text never reaches the statement.
SORT_COLUMNS = frozenset(
    {
        "duration_s",
        "ended_at",
        "profile",
        "run_id",
        "started_at",
        "status",
        "suite",
        "unit_serial",
    }
)


@dataclass(frozen=True)
class RunFilters:
    """Which runs a query is restricted to.

    ``after`` and ``before`` are inclusive bounds compared against
    ``started_at``. Both are ISO 8601, which sorts lexicographically, so a bare
    date such as ``2026-08-03`` bounds a whole day.
    """

    suite: str | None = None
    unit_serial: str | None = None
    status: tuple[str, ...] = ()
    after: str | None = None
    before: str | None = None


def _where(filters: RunFilters) -> tuple[str, list[Any]]:
    """SQL ``WHERE`` clause and its parameters for one filter set."""
    clauses: list[str] = []
    params: list[Any] = []
    if filters.suite:
        clauses.append("suite = ?")
        params.append(filters.suite)
    if filters.unit_serial:
        clauses.append("unit_serial = ?")
        params.append(filters.unit_serial)
    if filters.status:
        clauses.append(f"status IN ({', '.join('?' for _ in filters.status)})")
        params.extend(filters.status)
    if filters.after:
        clauses.append("started_at >= ?")
        params.append(filters.after)
    if filters.before:
        # A bare date must include the whole day, so the bound is the largest
        # string that starts with it.
        clauses.append("started_at <= ?")
        params.append(filters.before + "\uffff")
    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)


@dataclass
class RunRow:
    """One indexed run."""

    run_id: str
    suite: str
    status: str
    started_at: str
    run_dir: str
    ended_at: str | None = None
    duration_s: float | None = None
    verdict: str | None = None
    fail_reason: str | None = None
    profile: str | None = None
    target: str | None = None
    unit_serial: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "suite": self.suite,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": self.duration_s,
            "verdict": self.verdict,
            "fail_reason": self.fail_reason,
            "profile": self.profile,
            "target": self.target,
            "unit_serial": self.unit_serial,
            "run_dir": self.run_dir,
        }


class RunsIndex:
    """Thread-safe SQLite wrapper for the runs table."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(RUNS_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert(self, row: RunRow) -> None:
        """Insert or replace one run."""
        values = [getattr(row, column) for column in _COLUMNS]
        placeholders = ", ".join("?" for _ in _COLUMNS)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO runs ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                values,
            )
            self._conn.commit()

    def count(self, filters: RunFilters | None = None) -> int:
        """How many rows match, ignoring limit and offset."""
        where, params = _where(filters or RunFilters())
        with self._lock:
            row = self._conn.execute(f"SELECT COUNT(*) FROM runs {where}", params).fetchone()
        return int(row[0])

    def list(
        self,
        filters: RunFilters | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
        sort: str = "started_at",
        descending: bool = True,
    ) -> list[RunRow]:
        """Matching runs, sorted by one column.

        An unknown sort column falls back to ``started_at`` rather than
        interpolating caller text into the statement.
        """
        column = sort if sort in SORT_COLUMNS else "started_at"
        order = "DESC" if descending else "ASC"
        where, params = _where(filters or RunFilters())
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM runs {where} ORDER BY {column} {order}, run_id {order} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [_to_row(r) for r in rows]

    def get(self, run_id: str) -> RunRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return _to_row(row) if row else None

    def reconcile_stale(self) -> int:
        """Mark runs still recorded as in-progress as interrupted.

        Called on startup; a process that exited abnormally cannot update its
        own row.
        """
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE runs SET status = 'error', verdict = 'ERROR', "
                "fail_reason = 'interrupted: Gauntlet stopped while this run was in progress' "
                "WHERE status IN ('starting', 'running', 'stopping', 'aborting')"
            )
            self._conn.commit()
            return cursor.rowcount

    def import_tree(self, runs_dir: Path) -> int:
        """Index any run directory on disk that is not already known."""
        if not runs_dir.is_dir():
            return 0
        imported = 0
        for verdict_path in sorted(runs_dir.glob("*/*/verdict.json")):
            run_dir = verdict_path.parent
            run_id = run_dir.name
            if self.get(run_id) is not None:
                continue
            row = _row_from_disk(run_dir, verdict_path)
            if row is not None:
                self.upsert(row)
                imported += 1
        return imported


def _row_from_disk(run_dir: Path, verdict_path: Path) -> RunRow | None:
    verdict = _read_json(verdict_path) or {}
    manifest = _read_json(run_dir / "manifest.json") or {}
    if verdict.get("passed"):
        status, code = "passed", "PASS"
    elif verdict.get("aborted"):
        status, code = "aborted", "ABORTED"
    else:
        status, code = "failed", "FAIL"
    return RunRow(
        run_id=run_dir.name,
        suite=str(manifest.get("suite") or run_dir.parent.name),
        status=status,
        started_at=str(verdict.get("started_at_utc") or manifest.get("started_at_utc") or ""),
        run_dir=str(run_dir),
        ended_at=str(verdict.get("ended_at_utc") or "") or None,
        duration_s=_as_float(verdict.get("duration_s")),
        verdict=code,
        fail_reason=str(verdict.get("reason") or "") or None,
        profile=_basename(manifest.get("profile_path")),
        target=_as_str(manifest.get("target")),
        unit_serial=_as_str(manifest.get("unit_serial")),
    )


def _to_row(row: sqlite3.Row) -> RunRow:
    return RunRow(**dict(row))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _as_str(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _basename(value: Any) -> str | None:
    return Path(str(value)).name if isinstance(value, str) and value else None

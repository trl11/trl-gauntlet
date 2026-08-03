"""SQLite sink.

Records the same per-iteration data as ``metrics.jsonl`` in queryable form.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from gauntlet_suite.iteration import IterationContext, IterationOutcome
from gauntlet_suite.reporting.jsonl_sink import json_safe

_SCHEMA = """
CREATE TABLE IF NOT EXISTS iterations (
    iteration   INTEGER PRIMARY KEY,
    timestamp   REAL    NOT NULL,
    elapsed_s   REAL    NOT NULL,
    success     INTEGER NOT NULL,
    reason      TEXT,
    metrics     TEXT
);
CREATE TABLE IF NOT EXISTS phases (
    iteration   INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    elapsed_s   REAL    NOT NULL,
    success     INTEGER NOT NULL,
    error       TEXT,
    detail      TEXT,
    PRIMARY KEY (iteration, name)
);
"""


class EventsSink:
    """Records iterations and their phases into ``events.sqlite``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __call__(self, outcome: IterationOutcome, ctx: IterationContext) -> None:
        with self._lock:
            if self._conn is None:
                return
            cur = self._conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO iterations VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ctx.iteration,
                    time.time(),
                    ctx.elapsed_run_s,
                    int(outcome.success),
                    outcome.reason,
                    json.dumps(json_safe(outcome.metrics)),
                ),
            )
            for phase in outcome.phase_records:
                cur.execute(
                    "INSERT OR REPLACE INTO phases VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ctx.iteration,
                        phase.name,
                        phase.elapsed_s,
                        int(phase.success),
                        phase.error,
                        json.dumps(phase.detail),
                    ),
                )
            self._conn.commit()

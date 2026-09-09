"""SQLite sink.

Every record ``metrics.jsonl`` carries, in queryable form: one table per record
kind, and a ``metrics`` table holding each metric leaf as its own row, so a
value can be selected and plotted without reaching into JSON.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from gauntlet_sdk.iteration import IterationContext, IterationOutcome
from gauntlet_sdk.reporting.jsonl_sink import iteration_record

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
CREATE TABLE IF NOT EXISTS live (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL    NOT NULL,
    elapsed_s   REAL,
    metrics     TEXT
);
CREATE TABLE IF NOT EXISTS anomalies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL    NOT NULL,
    iteration   INTEGER,
    probe       TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    detail      TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    kind        TEXT    NOT NULL,
    iteration   INTEGER,
    timestamp   REAL    NOT NULL,
    elapsed_s   REAL,
    key         TEXT    NOT NULL,
    number      REAL,
    text        TEXT
);
CREATE INDEX IF NOT EXISTS metrics_key ON metrics (key);
CREATE INDEX IF NOT EXISTS metrics_iteration ON metrics (iteration);
CREATE VIEW IF NOT EXISTS metric_names AS
    SELECT key, kind, COUNT(*) AS samples, MIN(number) AS lowest, MAX(number) AS highest
    FROM metrics
    GROUP BY key, kind;
"""


class EventsSink:
    """Records what ``metrics.jsonl`` holds into ``events.sqlite``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __call__(self, outcome: IterationOutcome, ctx: IterationContext) -> None:
        """Record one iteration, for a suite driving its own sinks."""
        self.record(iteration_record(outcome, ctx))

    def record(self, record: dict[str, Any]) -> None:
        """Record one ``metrics.jsonl`` record, whichever kind it is."""
        with self._lock:
            if self._conn is None:
                return
            cursor = self._conn.cursor()
            kind = record.get("kind", "iteration")
            if kind == "iteration":
                self._write_iteration(cursor, record)
            elif kind == "live":
                self._write_live(cursor, record)
            elif kind == "anomaly":
                self._write_anomaly(cursor, record)
            self._conn.commit()

    def _write_iteration(self, cursor: sqlite3.Cursor, record: dict[str, Any]) -> None:
        iteration = record["iteration"]
        cursor.execute(
            "INSERT OR REPLACE INTO iterations VALUES (?, ?, ?, ?, ?, ?)",
            (
                iteration,
                record["timestamp"],
                record["elapsed_run_s"],
                int(record["success"]),
                record["reason"],
                json.dumps(record["metrics"]),
            ),
        )
        for phase in record.get("phases", []):
            cursor.execute(
                "INSERT OR REPLACE INTO phases VALUES (?, ?, ?, ?, ?, ?)",
                (
                    iteration,
                    phase["name"],
                    phase["elapsed_s"],
                    int(phase["success"]),
                    phase["error"],
                    json.dumps(phase["detail"]),
                ),
            )
        cursor.execute("DELETE FROM metrics WHERE iteration = ?", (iteration,))
        self._write_metrics(cursor, record, iteration)

    def _write_live(self, cursor: sqlite3.Cursor, record: dict[str, Any]) -> None:
        cursor.execute(
            "INSERT INTO live (timestamp, elapsed_s, metrics) VALUES (?, ?, ?)",
            (record["timestamp"], record["elapsed_run_s"], json.dumps(record["metrics"])),
        )
        self._write_metrics(cursor, record, None)

    def _write_anomaly(self, cursor: sqlite3.Cursor, record: dict[str, Any]) -> None:
        detail = record.get("detail")
        cursor.execute(
            "INSERT INTO anomalies (timestamp, iteration, probe, kind, detail) VALUES (?, ?, ?, ?, ?)",
            (
                record["timestamp"],
                detail.get("iteration") if isinstance(detail, dict) else None,
                record["probe"],
                record["anomaly_kind"],
                json.dumps(detail),
            ),
        )

    def _write_metrics(self, cursor: sqlite3.Cursor, record: dict[str, Any], iteration: int | None) -> None:
        cursor.executemany(
            "INSERT INTO metrics (kind, iteration, timestamp, elapsed_s, key, number, text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (record["kind"], iteration, record["timestamp"], record["elapsed_run_s"], key, number, text)
                for key, number, text in metric_leaves(record["metrics"])
            ],
        )


def metric_leaves(metrics: Any, prefix: str = "") -> Iterator[tuple[str, float | None, str | None]]:
    """Each metric leaf as a dotted key, its number and its text.

    A list is stored whole as JSON rather than a row per element: a captured
    signal runs to thousands of samples, and a row each would bury the scalars
    the table exists to make selectable.
    """
    if isinstance(metrics, dict):
        for name, value in metrics.items():
            yield from metric_leaves(value, f"{prefix}.{name}" if prefix else str(name))
        return
    if not prefix:
        return
    if isinstance(metrics, (int, float)):
        yield prefix, float(metrics), None
    elif metrics is None or isinstance(metrics, str):
        yield prefix, None, metrics
    else:
        yield prefix, None, json.dumps(metrics)

"""Recording events that are not iteration results.

Anomalies are written to ``metrics.jsonl`` alongside iteration records and do
not affect the pass/fail counters.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any

from gauntlet_suite.reporting.jsonl_sink import JsonlSink


class AnomalyLog:
    """Append-only anomaly writer with running counts. Thread-safe."""

    def __init__(self, sink: JsonlSink) -> None:
        self._sink = sink
        self._counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def record(
        self,
        probe: str,
        kind: str,
        *,
        iteration: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Record one anomaly against a named probe."""
        payload: dict[str, Any] = dict(detail or {})
        if iteration is not None:
            payload["iteration"] = iteration
        with self._lock:
            self._counts[probe] += 1
        self._sink.write_anomaly(probe, kind, payload)

    def counts(self) -> dict[str, int]:
        """Anomalies so far, per probe."""
        with self._lock:
            return dict(self._counts)

    def total(self) -> int:
        """Anomalies so far, across every probe."""
        with self._lock:
            return sum(self._counts.values())

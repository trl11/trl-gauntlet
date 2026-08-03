"""Streaming JSONL sink — the live channel to Gauntlet.

One JSON object per line in ``metrics.jsonl``. The file is line-buffered
because Gauntlet tails it while the run is in progress; a block-buffered
writer leaves the UI blank until the suite exits.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from gauntlet_sdk.iteration import IterationContext, IterationOutcome


class JsonlSink:
    """Appends one record per iteration to ``metrics.jsonl``.

    :meth:`write_live` and :meth:`write_anomaly` write the two non-iteration
    record kinds, neither of which affects the pass/fail counters.

    Thread-safe.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("a", buffering=1, encoding="utf-8")
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None  # type: ignore[assignment]

    def __call__(self, outcome: IterationOutcome, ctx: IterationContext) -> None:
        self._write(
            {
                "kind": "iteration",
                "iteration": ctx.iteration,
                "timestamp": time.time(),
                "elapsed_run_s": ctx.elapsed_run_s,
                "success": outcome.success,
                "reason": outcome.reason,
                "metrics": json_safe(outcome.metrics),
                "phases": [json_safe(asdict(p)) for p in outcome.phase_records],
            }
        )

    def write_live(self, metrics: dict[str, Any], *, elapsed_run_s: float | None = None) -> None:
        """Record telemetry sampled outside an iteration."""
        self._write(
            {
                "kind": "live",
                "timestamp": time.time(),
                "elapsed_run_s": elapsed_run_s,
                "metrics": json_safe(metrics),
            }
        )

    def write_anomaly(self, probe: str, kind: str, detail: Any = None) -> None:
        """Record a noteworthy event that is not itself an iteration result."""
        self._write(
            {
                "kind": "anomaly",
                "timestamp": time.time(),
                "probe": probe,
                "anomaly_kind": kind,
                "detail": json_safe(detail) if detail is not None else {},
            }
        )

    def _write(self, record: dict[str, Any]) -> None:
        with self._lock:
            if self._fh is None:
                return
            self._fh.write(json.dumps(record) + "\n")


def json_safe(value: Any) -> Any:
    """Coerce arbitrary values into something ``json.dumps`` accepts."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return repr(value)

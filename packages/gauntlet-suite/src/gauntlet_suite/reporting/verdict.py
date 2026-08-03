"""Writer for ``verdict.json``, the one required artifact.

Gauntlet records a run with no verdict file as an error.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from gauntlet_suite.iteration import RunResult

ResultFormat = Literal["bytes", "decimal", "duration", "int", "percent", "text"]
TestOutcome = Literal["error", "fail", "pass", "skip"]


def make_result(
    key: str,
    label: str,
    value: Any,
    *,
    format: ResultFormat = "text",
    unit: str | None = None,
    precision: int | None = None,
    highlight: bool = False,
) -> dict[str, Any]:
    """Build one headline figure for the run summary.

    ``format`` drives presentation: ``duration`` takes seconds and scales to
    ms/s/m/h, ``bytes`` scales to KB/MB/GB, ``percent`` takes 0-100, and
    ``text`` is shown verbatim.
    """
    entry: dict[str, Any] = {"key": key, "label": label, "value": value, "format": format}
    if unit is not None:
        entry["unit"] = unit
    if precision is not None:
        entry["precision"] = precision
    if highlight:
        entry["highlight"] = True
    return entry


def make_test(
    name: str,
    *,
    outcome: TestOutcome = "pass",
    classname: str | None = None,
    duration_s: float | None = None,
    message: str | None = None,
    traceback: str | None = None,
) -> dict[str, Any]:
    """Build one per-test row.

    ``message`` is the one-line summary; ``traceback`` is the expanded detail.
    """
    entry: dict[str, Any] = {"name": name, "outcome": outcome}
    if classname is not None:
        entry["classname"] = classname
    if duration_s is not None:
        entry["duration_s"] = round(float(duration_s), 4)
    if message:
        entry["message"] = message
    if traceback:
        entry["traceback"] = traceback
    return entry


def write_verdict(
    path: Path,
    result: RunResult,
    *,
    reason: str | None = None,
    results: list[dict[str, Any]] | None = None,
    tests: list[dict[str, Any]] | None = None,
) -> None:
    """Write a verdict derived from a completed :class:`RunResult`."""
    passed = result.passed
    if not passed and not reason:
        reason = result.abort_reason or f"{result.failures}/{result.total_iterations} iterations failed"
    payload: dict[str, Any] = {
        "passed": passed,
        "reason": reason or "",
        "total_iterations": result.total_iterations,
        "successes": result.successes,
        "failures": result.failures,
        "duration_s": round(result.duration_s, 3),
        "started_at_utc": _iso(result.started_at),
        "ended_at_utc": _iso(result.ended_at),
        "aborted": result.aborted,
        "abort_reason": result.abort_reason,
        "stopped_early": result.stopped_early,
    }
    if results:
        payload["results"] = list(results)
    if tests:
        payload["tests"] = list(tests)
    _dump(path, payload)


def write_simple_verdict(path: Path, *, passed: bool, reason: str = "", **extra: Any) -> None:
    """Write a verdict without a :class:`RunResult`.

    For suites that do not use the iteration loop. ``reason`` is required when
    ``passed`` is false.
    """
    payload: dict[str, Any] = {"passed": passed, "reason": reason}
    payload.update(extra)
    _dump(path, payload)


def _dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

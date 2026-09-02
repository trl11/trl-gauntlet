"""Background readers that turn a running suite into a live event stream.

Both run on threads and publish through :meth:`EventBus.publish_threadsafe`.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from gauntlet.supervisor.events import EventBus

# Tools that colourize despite NO_COLOR would otherwise put raw escape bytes
# into the captured log.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ERROR_RE = re.compile(r"\b(?:ERROR|FAILED|FATAL|Traceback)\b")
_WARN_RE = re.compile(r"\b(?:WARNING|WARN)\b", re.IGNORECASE)


def classify_log_line(line: str) -> tuple[str, str]:
    """Infer a level for one stdout line and strip any level prefix.

    An explicit ``error:`` or ``warn:`` prefix takes precedence over the
    whole-word fallback match.
    """
    lowered = line[:6].lower()
    if lowered.startswith("error:"):
        return "error", line[6:].lstrip()
    if lowered.startswith("warn:"):
        return "warning", line[5:].lstrip()
    if _ERROR_RE.search(line):
        return "error", line
    if _WARN_RE.search(line):
        return "warning", line
    return "info", line


def pump_stdout(proc: subprocess.Popen[str], bus: EventBus, log_path: Path) -> None:
    """Stream the suite's stdout to the bus and to ``test.log``."""
    stdout = proc.stdout
    if stdout is None:
        return
    handle = None
    try:
        with contextlib.suppress(OSError):
            handle = log_path.open("a", buffering=1, encoding="utf-8")
        for raw in iter(stdout.readline, ""):
            line = _ANSI_RE.sub("", raw.rstrip("\r\n"))
            if not line:
                continue
            level, message = classify_log_line(line)
            if handle is not None:
                with contextlib.suppress(OSError):
                    handle.write(line + "\n")
            bus.publish_threadsafe("log", level=level, message=message)
    finally:
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()


def tail_metrics(path: Path, proc: subprocess.Popen[str], bus: EventBus, *, poll_s: float = 0.25) -> None:
    """Follow ``metrics.jsonl`` until the suite exits and the file is drained."""
    handle = None
    position = 0
    try:
        while True:
            if handle is None and path.is_file():
                with contextlib.suppress(OSError):
                    handle = path.open("r", encoding="utf-8")
            if handle is not None:
                position = _drain(handle, position, bus)
            if proc.poll() is not None:
                # The writer is gone; one more pass picks up its final flush.
                time.sleep(0.2)
                if handle is None and path.is_file():
                    with contextlib.suppress(OSError):
                        handle = path.open("r", encoding="utf-8")
                if handle is not None:
                    _drain(handle, position, bus)
                return
            time.sleep(poll_s)
    finally:
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()


def _drain(handle: Any, position: int, bus: EventBus) -> int:
    """Publish every complete line from ``position``, returning the new offset.

    A trailing partial line is left unconsumed.
    """
    handle.seek(position)
    while True:
        line = handle.readline()
        if not line:
            return position
        if not line.endswith("\n"):
            handle.seek(position)
            return position
        publish_record(bus, line)
        position = handle.tell()


def publish_record(bus: EventBus, line: str) -> None:
    """Translate one metrics record into events."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(record, dict):
        return

    kind = record.get("kind") or "iteration"
    iteration = record.get("iteration")
    elapsed = record.get("elapsed_run_s")
    raw_metrics = record.get("metrics")
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}

    values = {name: float(value) for name, value in flatten(metrics) if isinstance(value, (bool, int, float))}
    if values:
        bus.publish_threadsafe("metrics", iteration=iteration, elapsed_s=elapsed, values=values)

    if kind == "live":
        return
    if kind == "anomaly":
        bus.publish_threadsafe(
            "anomaly",
            probe=record.get("probe", "?"),
            anomaly_kind=record.get("anomaly_kind", ""),
            detail=record.get("detail") or {},
        )
        return

    for phase in record.get("phases") or []:
        if isinstance(phase, dict):
            bus.publish_threadsafe(
                "phase",
                iteration=iteration,
                phase=phase.get("name", "?"),
                elapsed_s=phase.get("elapsed_s", 0.0),
                success=bool(phase.get("success", True)),
                detail=phase.get("detail") or {},
            )

    bus.publish_threadsafe(
        "iteration",
        iteration=iteration,
        elapsed_run_s=elapsed,
        success=bool(record.get("success", True)),
        reason=record.get("reason") or "",
        images=recorded_paths(metrics, "images"),
        traces=recorded_paths(metrics, "traces"),
    )


def recorded_paths(metrics: dict[str, Any], key: str) -> list[str]:
    """The run-relative paths a record listed under ``key``.

    Anything that is not a list of strings is nothing: a record naming a file
    badly costs that file, not the iteration it was recorded against.
    """
    listed = metrics.get(key)
    return [path for path in listed if isinstance(path, str)] if isinstance(listed, list) else []


def flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten nested metrics to dotted paths, keeping only scalar leaves."""
    out: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(flatten(child, path))
    elif isinstance(value, (bool, int, float)) and prefix:
        out.append((prefix, value))
    return out

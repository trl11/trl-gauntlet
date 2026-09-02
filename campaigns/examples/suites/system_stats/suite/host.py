"""Facts about the host that are not a sampled rate: processor name, uptime
and how many processes are running."""

from __future__ import annotations

import os
from pathlib import Path

from suite.procfs import PROC, read_text


def parse_cpu_model(text: str) -> str | None:
    """Processor name from ``/proc/cpuinfo``, or ``None`` when it names none.

    The key differs by architecture, so each candidate is tried in turn.
    """
    for key in ("model name", "Model", "cpu model", "Processor"):
        for line in text.splitlines():
            name, separator, value = line.partition(":")
            if separator and name.strip() == key and value.strip():
                return value.strip()
    return None


def parse_uptime(text: str) -> float | None:
    """Seconds since boot, from ``/proc/uptime``."""
    fields = text.split()
    if not fields:
        return None
    try:
        return float(fields[0])
    except ValueError:
        return None


def read_cpu_model(*, proc: Path = PROC) -> str | None:
    """Processor name, or ``None`` when ``/proc/cpuinfo`` names none."""
    text = read_text(proc / "cpuinfo")
    return parse_cpu_model(text) if text is not None else None


def read_process_count(*, proc: Path = PROC) -> int | None:
    """Processes on the system, counted from the pid directories in ``/proc``."""
    try:
        return sum(1 for entry in os.listdir(proc) if entry.isdigit())
    except OSError:
        return None


def read_uptime(*, proc: Path = PROC) -> float | None:
    """Seconds since boot, or ``None`` when ``/proc/uptime`` is unreadable."""
    text = read_text(proc / "uptime")
    return parse_uptime(text) if text is not None else None

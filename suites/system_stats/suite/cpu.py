"""CPU utilisation and load average.

``/proc/stat`` counts jiffies since boot, so utilisation is the difference
between two reads rather than a single reading. ``/proc/loadavg`` is already an
average and is read directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from suite.procfs import PROC, parse_int, parse_ints, read_text


@dataclass(frozen=True)
class CpuTimes:
    """Cumulative jiffies from one ``cpu`` line of ``/proc/stat``."""

    name: str
    idle: int
    total: int


@dataclass(frozen=True)
class CpuUsage:
    """Utilisation across the window between two ``/proc/stat`` reads."""

    overall_percent: float
    per_core_percent: dict[str, float]


@dataclass(frozen=True)
class LoadAverage:
    """The five fields of ``/proc/loadavg``."""

    fifteen: float
    five: float
    one: float
    runnable: int
    total: int


@dataclass(frozen=True)
class Stat:
    """The parts of ``/proc/stat`` this suite reports."""

    context_switches: int
    forks: int
    overall: CpuTimes
    per_core: tuple[CpuTimes, ...]
    procs_blocked: int
    procs_running: int


def cpu_usage(previous: Stat, current: Stat) -> CpuUsage | None:
    """Utilisation between two ``/proc/stat`` reads.

    Returns ``None`` when no jiffies elapsed between them, which is what a
    window too short to measure looks like. Cores that appeared since the
    previous read have nothing to subtract and are omitted.
    """
    overall = busy_percent(previous.overall, current.overall)
    if overall is None:
        return None
    before = {times.name: times for times in previous.per_core}
    per_core: dict[str, float] = {}
    for times in current.per_core:
        earlier = before.get(times.name)
        if earlier is None:
            continue
        busy = busy_percent(earlier, times)
        if busy is not None:
            per_core[times.name] = busy
    return CpuUsage(overall_percent=overall, per_core_percent=per_core)


def busy_percent(previous: CpuTimes, current: CpuTimes) -> float | None:
    """Share of the window the CPU spent doing something."""
    total = current.total - previous.total
    if total <= 0:
        return None
    idle = max(0, current.idle - previous.idle)
    return round(100.0 * (total - min(idle, total)) / total, 2)


def parse_loadavg(text: str) -> LoadAverage | None:
    """Load averages and the runnable/total process counts."""
    fields = text.split()
    if len(fields) < 4:
        return None
    try:
        averages = [float(field) for field in fields[:3]]
    except ValueError:
        return None
    runnable, _, total = fields[3].partition("/")
    return LoadAverage(
        fifteen=averages[2],
        five=averages[1],
        one=averages[0],
        runnable=parse_int(runnable) or 0,
        total=parse_int(total) or 0,
    )


def parse_stat(text: str) -> Stat | None:
    """CPU jiffies, context switches and process counters from ``/proc/stat``."""
    overall: CpuTimes | None = None
    per_core: list[CpuTimes] = []
    scalars: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        name = fields[0]
        if name == "cpu" or (name.startswith("cpu") and name[3:].isdigit()):
            times = _cpu_times(name, fields[1:])
            if times is None:
                continue
            if name == "cpu":
                overall = times
            else:
                per_core.append(times)
        elif name in {"ctxt", "processes", "procs_blocked", "procs_running"} and len(fields) > 1:
            value = parse_int(fields[1])
            if value is not None:
                scalars[name] = value
    if overall is None:
        return None
    return Stat(
        context_switches=scalars.get("ctxt", 0),
        forks=scalars.get("processes", 0),
        overall=overall,
        per_core=tuple(per_core),
        procs_blocked=scalars.get("procs_blocked", 0),
        procs_running=scalars.get("procs_running", 0),
    )


def read_loadavg(*, proc: Path = PROC) -> LoadAverage | None:
    """Load averages, or ``None`` when ``/proc/loadavg`` is unreadable."""
    text = read_text(proc / "loadavg")
    return parse_loadavg(text) if text is not None else None


def read_stat(*, proc: Path = PROC) -> Stat | None:
    """CPU and process counters, or ``None`` when ``/proc/stat`` is unreadable."""
    text = read_text(proc / "stat")
    return parse_stat(text) if text is not None else None


def _cpu_times(name: str, fields: list[str]) -> CpuTimes | None:
    """Fold one cpu line into busy and idle totals.

    Columns 4 and 5 are idle and iowait; both are time the CPU had nothing to
    run, so they count as idle together. A line with fewer than five columns is
    truncated and unusable.
    """
    values = parse_ints(fields)
    if values is None or len(values) < 5:
        return None
    return CpuTimes(name=name, idle=values[3] + values[4], total=sum(values))

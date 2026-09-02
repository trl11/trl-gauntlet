"""The ``metrics`` block of one JSONL record.

Readings the host does not offer are left out entirely rather than written as
null, so a plot only ever shows measurements that were taken. Keys are slugs
rather than paths, because Gauntlet flattens numeric leaves to dotted names.
"""

from __future__ import annotations

from typing import Any

from suite.cpu import LoadAverage
from suite.memory import Memory
from suite.sampler import Sample


def to_metrics(sample: Sample) -> dict[str, Any]:
    """Flatten one sample into plottable numbers."""
    metrics: dict[str, Any] = {"cpu_count": sample.cpu_count, "window_s": sample.window_s}
    if sample.cpu is not None:
        metrics["cpu"] = {"percent": sample.cpu.overall_percent, "per_core": dict(sample.cpu.per_core_percent)}
    if sample.context_switches_per_s is not None:
        metrics["context_switches_per_s"] = sample.context_switches_per_s
    if sample.load is not None:
        metrics["load"] = _load(sample.load, sample.load_per_core or 0.0)
    if sample.memory is not None:
        metrics["memory"] = _memory(sample.memory)
        metrics["swap"] = _swap(sample.memory)
    if sample.disks:
        metrics["disk"] = _disks(sample)
    if sample.thermal:
        metrics["thermal"] = {slug(zone.label): round(zone.celsius, 2) for zone in sample.thermal}
        hottest = sample.hottest
        if hottest is not None:
            metrics["thermal_max_c"] = round(hottest.celsius, 2)
    if sample.network:
        metrics["net"] = _network(sample)
    processes = _processes(sample)
    if processes:
        metrics["processes"] = processes
    if sample.uptime_s is not None:
        metrics["uptime_s"] = round(sample.uptime_s, 1)
    return metrics


def slug(text: str) -> str:
    """Turn a mount point, interface or zone label into a metric key."""
    cleaned = "".join(character if character.isalnum() else "_" for character in text).strip("_")
    return cleaned.lower() or "root"


def _disks(sample: Sample) -> dict[str, dict[str, float]]:
    return {
        slug(disk.mount_point): {
            "free_bytes": disk.free_bytes,
            "free_percent": round(disk.free_percent, 2),
            "total_bytes": disk.total_bytes,
            "used_bytes": disk.used_bytes,
        }
        for disk in sample.disks
    }


def _load(load: LoadAverage, per_core: float) -> dict[str, float]:
    return {
        "fifteen": load.fifteen,
        "five": load.five,
        "one": load.one,
        "per_core": round(per_core, 3),
        "runnable": load.runnable,
    }


def _memory(memory: Memory) -> dict[str, float]:
    return {
        "available_bytes": memory.available_bytes,
        "available_percent": round(memory.available_percent, 2),
        "total_bytes": memory.total_bytes,
        "used_bytes": memory.used_bytes,
        "used_percent": round(memory.used_percent, 2),
    }


def _network(sample: Sample) -> dict[str, dict[str, float]]:
    window = sample.window_s
    rows: dict[str, dict[str, float]] = {}
    for name, counters in sample.network.items():
        growth = sample.network_growth.get(name)
        rows[slug(name)] = {
            "dropped": counters.dropped,
            "errors": counters.errors,
            "new_dropped": growth.dropped if growth else 0,
            "new_errors": growth.errors if growth else 0,
            "rx_bytes_per_s": round(growth.rx_bytes / window, 2) if growth and window > 0 else 0.0,
            "tx_bytes_per_s": round(growth.tx_bytes / window, 2) if growth and window > 0 else 0.0,
        }
    return rows


def _processes(sample: Sample) -> dict[str, int]:
    processes: dict[str, int] = {}
    if sample.process_count is not None:
        processes["count"] = sample.process_count
    if sample.procs_running is not None:
        processes["running"] = sample.procs_running
    if sample.procs_blocked is not None:
        processes["blocked"] = sample.procs_blocked
    return processes


def _swap(memory: Memory) -> dict[str, float]:
    return {
        "total_bytes": memory.swap_total_bytes,
        "used_bytes": memory.swap_used_bytes,
        "used_percent": round(memory.swap_used_percent, 2),
    }

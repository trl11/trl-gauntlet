"""Readers for what the host is doing, using nothing but the standard library.

Everything here reads ``/proc``, ``/sys``, or ``os``/``shutil`` directly. Every
reader answers ``None`` or an empty list where the kernel does not offer the
file, so a container or a platform without ``/proc`` still gets a well-formed
answer instead of an exception.
"""

from __future__ import annotations

import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROC = Path("/proc")
_THERMAL = Path("/sys/class/thermal")

# Mounts backed by storage, as opposed to the kernel's own virtual filesystems.
_REAL_FILESYSTEMS = frozenset(
    {
        "btrfs",
        "exfat",
        "ext2",
        "ext3",
        "ext4",
        "f2fs",
        "hfsplus",
        "jfs",
        "ntfs",
        "ntfs3",
        "overlay",
        "vfat",
        "xfs",
        "zfs",
    }
)


def boot_time() -> str | None:
    """When the kernel started, from the ``btime`` line of ``/proc/stat``."""
    for line in (_read_text(_PROC / "stat") or "").splitlines():
        if line.startswith("btime "):
            seconds = _as_int(line.split()[1])
            if seconds is not None:
                return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def cpu_model() -> str | None:
    """Processor name, however this architecture spells it in ``/proc/cpuinfo``."""
    for line in (_read_text(_PROC / "cpuinfo") or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() in {"Model", "cpu model", "model name"} and value.strip():
            return value.strip()
    return None


def cpu_percent(
    previous: dict[str, tuple[int, int]],
    current: dict[str, tuple[int, int]],
) -> tuple[float | None, list[float]]:
    """Busy percentages overall and per core, between two ``cpu_times`` readings."""
    if not previous or not current:
        return None, []
    cores = [name for name in current if name != "cpu"]
    cores.sort(key=_core_number)
    per_core = []
    for name in cores:
        busy = _busy_percent(previous.get(name), current.get(name))
        per_core.append(busy if busy is not None else 0.0)
    return _busy_percent(previous.get("cpu"), current.get("cpu")), per_core


def cpu_times() -> dict[str, tuple[int, int]]:
    """Idle and total jiffies per CPU line of ``/proc/stat``."""
    times: dict[str, tuple[int, int]] = {}
    for line in (_read_text(_PROC / "stat") or "").splitlines():
        fields = line.split()
        if not fields or not fields[0].startswith("cpu"):
            continue
        values = [_as_int(field) for field in fields[1:]]
        numbers = [value for value in values if value is not None]
        if len(numbers) < 5:
            continue
        # Fields are user, nice, system, idle, iowait, ...; idle and iowait
        # are both time the CPU had nothing to do.
        times[fields[0]] = (numbers[3] + numbers[4], sum(numbers))
    return times


def disks() -> list[dict[str, Any]]:
    """Usage for every mount backed by real storage."""
    mounted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in (_read_text(_PROC / "mounts") or "").splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[2] not in _REAL_FILESYSTEMS:
            continue
        mount = fields[1].replace("\\040", " ")
        # A container bind-mounts single files such as /etc/hostname from the
        # host filesystem; they are the same volume under another name.
        try:
            is_dir = Path(mount).is_dir()
        except OSError:
            # Docker's overlay2 "merged" dirs are listed in /proc/mounts but
            # not readable by an unprivileged user; skip rather than crash.
            continue
        if mount in seen or not is_dir:
            continue
        seen.add(mount)
        usage = _disk_usage(mount)
        if usage is not None:
            mounted.append(usage)
    if not mounted:
        root = _disk_usage("/")
        if root is not None:
            mounted.append(root)
    return sorted(mounted, key=lambda disk: str(disk["mount"]))


def load_avg() -> list[float] | None:
    """One, five, and fifteen minute load averages."""
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except OSError:
        return None


def memory() -> dict[str, Any]:
    """Physical memory in bytes, with the share in use."""
    values = meminfo()
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return {"total": total, "available": available, "used": None, "percent": None}
    used = max(total - available, 0)
    return {
        "total": total,
        "available": available,
        "used": used,
        "percent": round(100.0 * used / total, 1) if total else 0.0,
    }


def meminfo() -> dict[str, int]:
    """``/proc/meminfo`` in bytes, keyed by its own labels."""
    values: dict[str, int] = {}
    for line in (_read_text(_PROC / "meminfo") or "").splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        amount = _as_int(fields[0]) if fields else None
        if amount is None:
            continue
        values[key.strip()] = amount * 1024 if fields[-1] == "kB" else amount
    return values


def process_count() -> int | None:
    """How many processes the kernel currently lists."""
    try:
        return sum(1 for entry in _PROC.iterdir() if entry.name.isdigit())
    except OSError:
        return None


def static_info(gauntlet_version: str, python_version: str) -> dict[str, Any]:
    """Host facts that do not change while the app is running."""
    return {
        "arch": platform.machine() or None,
        "boot_time": boot_time(),
        "cpu_count": os.cpu_count(),
        "cpu_model": cpu_model(),
        "gauntlet": gauntlet_version,
        "hostname": platform.node() or None,
        "kernel": platform.release() or None,
        "memory_total_bytes": meminfo().get("MemTotal"),
        "os": platform.platform(),
        "python": python_version,
    }


def swap() -> dict[str, Any]:
    """Swap in bytes, with the share in use."""
    values = meminfo()
    total = values.get("SwapTotal")
    free = values.get("SwapFree")
    if total is None or free is None:
        return {"total": total, "used": None, "percent": None}
    used = max(total - free, 0)
    return {
        "total": total,
        "used": used,
        "percent": round(100.0 * used / total, 1) if total else 0.0,
    }


def temperatures() -> list[dict[str, Any]]:
    """Every thermal zone the kernel exposes, in degrees Celsius."""
    readings: list[dict[str, Any]] = []
    for zone in _thermal_zones():
        milli = _as_int((_read_text(zone / "temp") or "").strip())
        if milli is None:
            continue
        label = (_read_text(zone / "type") or "").strip() or zone.name
        readings.append({"label": label, "celsius": round(milli / 1000.0, 1)})
    return readings


def uptime() -> float | None:
    """Seconds since boot, from ``/proc/uptime``."""
    fields = (_read_text(_PROC / "uptime") or "").split()
    if not fields:
        return None
    try:
        return round(float(fields[0]), 1)
    except ValueError:
        return None


def _as_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _core_number(name: str) -> int:
    """Sort ``cpu2`` before ``cpu10`` rather than by name."""
    number = _as_int(name[3:])
    return number if number is not None else 0


def _busy_percent(before: tuple[int, int] | None, after: tuple[int, int] | None) -> float | None:
    """Share of the interval between two ``/proc/stat`` readings spent busy."""
    if before is None or after is None:
        return None
    idle = after[0] - before[0]
    total = after[1] - before[1]
    if total <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1.0 - idle / total))), 1)


def _disk_usage(mount: str) -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage(mount)
    except OSError:
        return None
    return {
        "mount": mount,
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": round(100.0 * usage.used / usage.total, 1) if usage.total else 0.0,
    }


def _read_text(path: Path) -> str | None:
    """File contents, or None when the kernel does not offer this file here."""
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def _thermal_zones() -> list[Path]:
    try:
        return sorted(_THERMAL.glob("thermal_zone*"))
    except OSError:
        return []

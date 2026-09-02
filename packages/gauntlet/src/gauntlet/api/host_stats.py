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
import socket
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROC = Path("/proc")
_SYS_NET = Path("/sys/class/net")

# An interface's IPv4 address is the one thing here the kernel publishes in
# neither /proc nor /sys, so it is asked for over a socket instead. fcntl is
# Unix-only, and a platform without it reports no address rather than failing
# to import.
try:
    import fcntl

    _SIOCGIFADDR = 0x8915
except ImportError:  # pragma: no cover - not reachable on the hosts this runs on
    fcntl = None  # type: ignore[assignment]
    _SIOCGIFADDR = 0
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


def interfaces() -> list[dict[str, Any]]:
    """Every network interface the kernel counts, with its traffic and errors.

    Loopback is left out for the same reason ``disks()`` leaves out the
    kernel's virtual filesystems: it is always present, always healthy, and
    never what someone looking at a bench wants to know about.

    The counters are cumulative since boot and 32-bit on some drivers, so a
    caller comparing two samples has to expect them to wrap.
    """
    counters: list[dict[str, Any]] = []
    for line in (_read_text(_PROC / "net" / "dev") or "").splitlines():
        name, separator, rest = line.partition(":")
        if not separator:
            continue
        name = name.strip()
        fields = rest.split()
        if name == "lo" or len(fields) < 12:
            continue
        values = [_as_int(field) for field in fields[:12]]
        if any(value is None for value in values):
            continue
        counters.append(
            {
                "address": _ipv4_address(name),
                "name": name,
                "state": (_read_text(_SYS_NET / name / "operstate") or "").strip() or "unknown",
                "rx_bytes": values[0],
                "rx_packets": values[1],
                "rx_errors": values[2],
                "rx_dropped": values[3],
                "tx_bytes": values[8],
                "tx_packets": values[9],
                "tx_errors": values[10],
                "tx_dropped": values[11],
            }
        )
    return sorted(counters, key=lambda interface: str(interface["name"]))


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


def _ipv4_address(name: str) -> str | None:
    """The interface's primary IPv4 address, or None where it has none.

    An interface can carry several; this reports the first, which is the one
    the kernel answers SIOCGIFADDR with and the one a bench is reached on.
    """
    if fcntl is None:
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            packed = fcntl.ioctl(
                sock.fileno(),
                _SIOCGIFADDR,
                struct.pack("256s", name[:15].encode()),
            )
    except OSError:
        # No address assigned, or a name the kernel does not know.
        return None
    return socket.inet_ntoa(packed[20:24])


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


def disk_for(path: Path) -> dict[str, Any] | None:
    """Usage of the filesystem a given path is written to.

    The mount with the longest matching prefix wins, because that is the one a
    write to the path actually lands on. A container bind-mounts one host
    volume under several names, so picking the fullest mount instead would name
    whichever of them happened to sort first — accurate as a reading, and
    meaningless as a label.

    The nearest existing parent is measured, so a directory Gauntlet has not
    created yet still reports the disk it will be created on.
    """
    target = _nearest_existing(path)
    if target is None:
        return None
    within = [disk for disk in disks() if _is_within(target, str(disk["mount"]))]
    if within:
        return max(within, key=lambda disk: len(str(disk["mount"])))
    return _disk_usage(str(target))


def _nearest_existing(path: Path) -> Path | None:
    """``path`` if it exists, else the closest parent that does."""
    try:
        current = path.resolve()
    except OSError:
        return None
    for candidate in [current, *current.parents]:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            return None
    return None


def _is_within(path: Path, mount: str) -> bool:
    try:
        path.relative_to(mount)
    except ValueError:
        return False
    return True


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

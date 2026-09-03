"""Disk usage of the real filesystems listed in ``/proc/mounts``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from suite.procfs import PROC, read_text

# Kernel interfaces and in-memory filesystems: mounted, but not storage whose
# free space means anything to an operator.
PSEUDO_FILESYSTEMS = frozenset(
    {
        "autofs",
        "binfmt_misc",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fuse.gvfsd-fuse",
        "fuse.portal",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "nsfs",
        "proc",
        "pstore",
        "ramfs",
        "rpc_pipefs",
        "securityfs",
        "selinuxfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)

# /proc/mounts escapes these characters in device and mount-point fields.
MOUNT_ESCAPES = {"\\011": "\t", "\\012": "\n", "\\040": " ", "\\134": "\\"}


@dataclass(frozen=True)
class Disk:
    """Space on one mounted filesystem."""

    device: str
    filesystem: str
    free_bytes: int
    mount_point: str
    total_bytes: int
    used_bytes: int

    @property
    def free_percent(self) -> float:
        """Share of the filesystem still available to an unprivileged user."""
        return 100.0 * self.free_bytes / self.total_bytes if self.total_bytes else 0.0

    @property
    def used_percent(self) -> float:
        return 100.0 * self.used_bytes / self.total_bytes if self.total_bytes else 0.0


@dataclass(frozen=True)
class Mount:
    """One line of ``/proc/mounts``."""

    device: str
    filesystem: str
    mount_point: str


def parse_mounts(text: str) -> tuple[Mount, ...]:
    """Mounted filesystems from ``/proc/mounts``, pseudo-filesystems dropped.

    Mount order is preserved, so the earliest mount of a device comes first.
    """
    mounts: list[Mount] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        filesystem = fields[2]
        if filesystem in PSEUDO_FILESYSTEMS:
            continue
        mounts.append(
            Mount(
                device=_unescape(fields[0]),
                filesystem=filesystem,
                mount_point=_unescape(fields[1]),
            )
        )
    return tuple(mounts)


def read_disks(*, proc: Path = PROC) -> tuple[Disk, ...]:
    """Space on every real filesystem mounted at a directory.

    One entry per device: bind mounts and overlays of the same device report
    the same numbers, so only the earliest mount of each is kept. A mount point
    that is a file — the way a container binds ``/etc/hosts`` — reports the
    space of the filesystem behind it and is dropped for the same reason.
    """
    text = read_text(proc / "mounts")
    if text is None:
        return ()
    disks: list[Disk] = []
    seen: set[str] = set()
    for mount in parse_mounts(text):
        if mount.device in seen or not _is_directory(mount.mount_point):
            continue
        seen.add(mount.device)
        usage = statvfs_usage(mount.mount_point)
        if usage is None or usage[0] <= 0:
            continue
        total, used, free = usage
        disks.append(
            Disk(
                device=mount.device,
                filesystem=mount.filesystem,
                free_bytes=free,
                mount_point=mount.mount_point,
                total_bytes=total,
                used_bytes=used,
            )
        )
    return tuple(disks)


def statvfs_usage(mount_point: str) -> tuple[int, int, int] | None:
    """Total, used and free bytes for a mount point.

    Free space is what an unprivileged user may still allocate, so it excludes
    the blocks reserved for root and is smaller than total minus used.
    """
    try:
        stats = os.statvfs(mount_point)
    except OSError:
        return None
    total = stats.f_blocks * stats.f_frsize
    free = stats.f_bavail * stats.f_frsize
    used = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
    return total, used, free


def _is_directory(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def _unescape(field: str) -> str:
    for escape, character in MOUNT_ESCAPES.items():
        field = field.replace(escape, character)
    return field

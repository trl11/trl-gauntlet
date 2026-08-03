"""Memory and swap, from ``/proc/meminfo``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from suite.procfs import PROC, parse_int, read_text


@dataclass(frozen=True)
class Memory:
    """Byte counts derived from ``/proc/meminfo``."""

    available_bytes: int
    buffers_bytes: int
    cached_bytes: int
    free_bytes: int
    swap_free_bytes: int
    swap_total_bytes: int
    total_bytes: int

    @property
    def available_percent(self) -> float:
        return 100.0 * self.available_bytes / self.total_bytes if self.total_bytes else 0.0

    @property
    def swap_used_bytes(self) -> int:
        return max(0, self.swap_total_bytes - self.swap_free_bytes)

    @property
    def swap_used_percent(self) -> float:
        return 100.0 * self.swap_used_bytes / self.swap_total_bytes if self.swap_total_bytes else 0.0

    @property
    def used_bytes(self) -> int:
        return max(0, self.total_bytes - self.available_bytes)

    @property
    def used_percent(self) -> float:
        return 100.0 * self.used_bytes / self.total_bytes if self.total_bytes else 0.0


def parse_meminfo(text: str) -> Memory | None:
    """Memory and swap totals in bytes.

    ``MemAvailable`` is absent before Linux 3.14, so free plus reclaimable
    buffers and cache stands in for it there.
    """
    values = _amounts(text)
    total = values.get("MemTotal")
    if total is None:
        return None
    free = values.get("MemFree", 0)
    buffers = values.get("Buffers", 0)
    cached = values.get("Cached", 0)
    return Memory(
        available_bytes=values.get("MemAvailable", free + buffers + cached),
        buffers_bytes=buffers,
        cached_bytes=cached,
        free_bytes=free,
        swap_free_bytes=values.get("SwapFree", 0),
        swap_total_bytes=values.get("SwapTotal", 0),
        total_bytes=total,
    )


def read_meminfo(*, proc: Path = PROC) -> Memory | None:
    """Memory and swap, or ``None`` when ``/proc/meminfo`` is unreadable."""
    text = read_text(proc / "meminfo")
    return parse_meminfo(text) if text is not None else None


def _amounts(text: str) -> dict[str, int]:
    """Every ``Name: value kB`` line of ``/proc/meminfo``, converted to bytes."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, rest = line.partition(":")
        if not separator:
            continue
        fields = rest.split()
        amount = parse_int(fields[0]) if fields else None
        if amount is None:
            continue
        scale = 1024 if len(fields) > 1 and fields[1].lower() == "kb" else 1
        values[key.strip()] = amount * scale
    return values

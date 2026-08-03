"""Reading the pseudo-files under ``/proc`` and ``/sys``.

Every reader in this suite goes through :func:`read_text`, which returns
``None`` instead of raising when a file is absent, unreadable or a directory.
A host that does not expose a statistic therefore degrades to a missing
reading rather than failing the run.
"""

from __future__ import annotations

from pathlib import Path

PROC = Path("/proc")
SYS = Path("/sys")


def parse_int(text: str) -> int | None:
    """One integer field, or ``None`` when it does not hold one."""
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def parse_ints(fields: list[str]) -> list[int] | None:
    """Every field as an integer, or ``None`` when any of them is not."""
    numbers: list[int] = []
    for field in fields:
        value = parse_int(field)
        if value is None:
            return None
        numbers.append(value)
    return numbers


def read_text(path: Path) -> str | None:
    """Contents of a pseudo-file, or ``None`` when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

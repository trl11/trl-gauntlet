"""Temperatures, from ``/sys/class/thermal``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from suite.procfs import SYS, parse_int, read_text

_ZONE_NUMBER = re.compile(r"(\d+)$")


@dataclass(frozen=True)
class ThermalZone:
    """One zone under ``/sys/class/thermal``."""

    celsius: float
    label: str
    name: str


def read_thermal(*, sys_root: Path = SYS) -> tuple[ThermalZone, ...]:
    """Every readable zone, in zone-number order.

    Containers and virtual machines usually expose none, which is an empty
    result rather than an error.
    """
    root = sys_root / "class" / "thermal"
    try:
        entries = [entry for entry in root.iterdir() if entry.name.startswith("thermal_zone")]
    except OSError:
        return ()
    zones: list[ThermalZone] = []
    for entry in sorted(entries, key=_zone_order):
        raw = read_text(entry / "temp")
        millidegrees = parse_int(raw.strip()) if raw is not None else None
        if millidegrees is None:
            continue
        label = read_text(entry / "type")
        zones.append(
            ThermalZone(
                celsius=millidegrees / 1000.0,
                label=(label or "").strip() or entry.name,
                name=entry.name,
            )
        )
    return tuple(zones)


def _zone_order(path: Path) -> tuple[int, str]:
    """Sort ``thermal_zone2`` before ``thermal_zone10``."""
    match = _ZONE_NUMBER.search(path.name)
    return (int(match.group(1)) if match else 1 << 30, path.name)

"""Shared test fixtures.

The suite package lives beside these tests rather than being installed, so the
suite directory goes on ``sys.path`` the same way Gauntlet puts it there for a
real run. ``fixtures/`` holds text captured from ``/proc`` on a Linux host, so
the readers are exercised against known input instead of the live system.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SUITE_ROOT = Path(__file__).resolve().parents[1]

if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from suite.sampler import Sample  # noqa: E402


@pytest.fixture
def fixture_text() -> Callable[[str], str]:
    """Read one captured pseudo-file by name."""

    def read(name: str) -> str:
        return (FIXTURES / name).read_text()

    return read


@pytest.fixture
def make_sample() -> Callable[..., Sample]:
    """Build a :class:`Sample` with everything absent unless named."""

    def build(**overrides: Any) -> Sample:
        defaults: dict[str, Any] = {
            "context_switches_per_s": None,
            "cpu": None,
            "cpu_count": 4,
            "disks": (),
            "load": None,
            "memory": None,
            "network": {},
            "network_growth": {},
            "process_count": None,
            "procs_blocked": None,
            "procs_running": None,
            "thermal": (),
            "uptime_s": None,
            "window_s": 1.0,
        }
        return Sample(**{**defaults, **overrides})

    return build


@pytest.fixture
def proc_tree(tmp_path: Path) -> Path:
    """A fake ``/proc`` built from the captured fixtures.

    ``mounts`` names ``tmp_path`` so the disk reader measures a directory that
    exists on every machine the tests run on.
    """
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    for name in ("cpuinfo", "loadavg", "meminfo", "stat", "uptime"):
        (proc / name).write_text((FIXTURES / f"{name}.txt").read_text())
    (proc / "net" / "dev").write_text((FIXTURES / "net_dev.txt").read_text())
    (proc / "mounts").write_text(f"/dev/fixture {tmp_path} ext4 rw,relatime 0 0\nproc /proc proc rw 0 0\n")
    for pid in ("1", "42", "1337"):
        (proc / pid).mkdir()
    (proc / "self").mkdir()
    return proc


@pytest.fixture
def sys_tree(tmp_path: Path) -> Path:
    """A fake ``/sys`` with two thermal zones and one unreadable zone."""
    thermal = tmp_path / "sys" / "class" / "thermal"
    thermal.mkdir(parents=True)
    for name, label, millidegrees in (
        ("thermal_zone0", "x86_pkg_temp", "42500"),
        ("thermal_zone10", "acpitz", "51000"),
    ):
        zone = thermal / name
        zone.mkdir()
        (zone / "type").write_text(f"{label}\n")
        (zone / "temp").write_text(f"{millidegrees}\n")
    broken = thermal / "thermal_zone2"
    broken.mkdir()
    (broken / "type").write_text("broken\n")
    (thermal / "cooling_device0").mkdir()
    return tmp_path / "sys"

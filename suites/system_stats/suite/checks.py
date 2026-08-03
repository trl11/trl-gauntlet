"""Per-sample checks.

Each check compares one reading against one threshold from the profile and
returns a :class:`CheckResult`. A ceiling fails when the reading is above it, a
floor fails when the reading is below it, and a reading the host does not offer
is skipped rather than failed — a container with no thermal zones is not a
faulty host.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from suite.profile import SystemStatsProfile
from suite.sampler import Sample

CheckStatus = Literal["fail", "pass", "skip"]


@dataclass(frozen=True)
class CheckResult:
    """What one check made of one sample."""

    label: str
    name: str
    status: CheckStatus
    detail: dict[str, Any] = field(default_factory=dict)
    kind: str = ""
    reason: str = ""

    @property
    def failed(self) -> bool:
        return self.status == "fail"


def check_cpu(sample: Sample, *, ceiling_percent: float) -> CheckResult:
    """Overall CPU utilisation stays below the ceiling."""
    if sample.cpu is None:
        return _skip("cpu", "CPU utilisation", "no CPU delta available for this window")
    observed = sample.cpu.overall_percent
    if observed <= ceiling_percent:
        return _pass("cpu", "CPU utilisation")
    busiest_core = max(sample.cpu.per_core_percent.items(), key=lambda item: item[1], default=("", 0.0))
    return _fail(
        "cpu",
        "CPU utilisation",
        "utilisation_above_ceiling",
        f"cpu utilisation {observed:.1f}% exceeds ceiling {ceiling_percent:.1f}%",
        {"ceiling_percent": ceiling_percent, "busiest_core": busiest_core[0], "percent": observed},
    )


def check_disk(sample: Sample, *, floor_free_percent: float) -> CheckResult:
    """Every mounted filesystem keeps at least the floor share free."""
    tightest = sample.tightest_disk
    if tightest is None:
        return _skip("disk", "Disk free space", "no mounted filesystems reported")
    observed = tightest.free_percent
    if observed >= floor_free_percent:
        return _pass("disk", "Disk free space")
    return _fail(
        "disk",
        "Disk free space",
        "free_space_below_floor",
        f"{tightest.mount_point} has {observed:.1f}% free, below floor {floor_free_percent:.1f}%",
        {
            "device": tightest.device,
            "floor_percent": floor_free_percent,
            "free_bytes": tightest.free_bytes,
            "free_percent": round(observed, 2),
            "mount_point": tightest.mount_point,
        },
    )


def check_load(sample: Sample, *, ceiling_per_core: float) -> CheckResult:
    """The one-minute load average per core stays below the ceiling."""
    observed = sample.load_per_core
    if observed is None or sample.load is None:
        return _skip("load", "Load average", "no load average available")
    if observed <= ceiling_per_core:
        return _pass("load", "Load average")
    return _fail(
        "load",
        "Load average",
        "load_above_ceiling",
        f"load {sample.load.one:.2f} over {sample.cpu_count} cores is {observed:.2f} per core, "
        f"above ceiling {ceiling_per_core:.2f}",
        {
            "ceiling_per_core": ceiling_per_core,
            "cpu_count": sample.cpu_count,
            "load_one": sample.load.one,
            "load_per_core": round(observed, 3),
        },
    )


def check_memory(sample: Sample, *, floor_available_percent: float) -> CheckResult:
    """Available memory stays above the floor share of total."""
    if sample.memory is None:
        return _skip("memory", "Available memory", "no memory reading available")
    observed = sample.memory.available_percent
    if observed >= floor_available_percent:
        return _pass("memory", "Available memory")
    return _fail(
        "memory",
        "Available memory",
        "available_below_floor",
        f"available memory {observed:.1f}% is below floor {floor_available_percent:.1f}%",
        {
            "available_bytes": sample.memory.available_bytes,
            "available_percent": round(observed, 2),
            "floor_percent": floor_available_percent,
            "total_bytes": sample.memory.total_bytes,
        },
    )


def check_network(sample: Sample, *, max_new_errors: int) -> CheckResult:
    """No interface accumulates more errors and drops than the budget allows."""
    if not sample.network_growth:
        return _skip("network", "Interface errors", "no interface counters to compare yet")
    name = ""
    worst = 0
    for interface, growth in sample.network_growth.items():
        if growth.errors + growth.dropped > worst:
            name = interface
            worst = growth.errors + growth.dropped
    if worst <= max_new_errors:
        return _pass("network", "Interface errors")
    counters = sample.network_growth[name]
    return _fail(
        "network",
        "Interface errors",
        "counters_increased",
        f"{name} reported {counters.errors} new errors and {counters.dropped} new drops "
        f"since the last sample, above the budget of {max_new_errors}",
        {
            "budget": max_new_errors,
            "interface": name,
            "new_dropped": counters.dropped,
            "new_errors": counters.errors,
        },
    )


def check_thermal(sample: Sample, *, ceiling_c: float) -> CheckResult:
    """The hottest thermal zone stays below the ceiling."""
    hottest = sample.hottest
    if hottest is None:
        return _skip("thermal", "Thermal zones", "host exposes no thermal zones")
    if hottest.celsius <= ceiling_c:
        return _pass("thermal", "Thermal zones")
    return _fail(
        "thermal",
        "Thermal zones",
        "temperature_above_ceiling",
        f"{hottest.label} at {hottest.celsius:.1f}C exceeds ceiling {ceiling_c:.1f}C",
        {
            "ceiling_c": ceiling_c,
            "celsius": round(hottest.celsius, 2),
            "zone": hottest.name,
            "zone_label": hottest.label,
        },
    )


def run_checks(sample: Sample, profile: SystemStatsProfile) -> list[CheckResult]:
    """Every check, in a stable order."""
    return [
        check_cpu(sample, ceiling_percent=profile.max_cpu_percent),
        check_disk(sample, floor_free_percent=profile.min_free_disk_percent),
        check_load(sample, ceiling_per_core=profile.max_load_per_core),
        check_memory(sample, floor_available_percent=profile.min_available_memory_percent),
        check_network(sample, max_new_errors=profile.max_new_interface_errors),
        check_thermal(sample, ceiling_c=profile.max_temperature_c),
    ]


def _fail(name: str, label: str, kind: str, reason: str, detail: dict[str, Any]) -> CheckResult:
    return CheckResult(detail=detail, kind=kind, label=label, name=name, reason=reason, status="fail")


def _pass(name: str, label: str) -> CheckResult:
    return CheckResult(label=label, name=name, status="pass")


def _skip(name: str, label: str, reason: str) -> CheckResult:
    return CheckResult(label=label, name=name, reason=reason, status="skip")

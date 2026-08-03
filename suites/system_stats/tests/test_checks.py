"""Threshold tests: each check at the boundary and one step past it."""

from __future__ import annotations

from collections.abc import Callable

from suite.checks import (
    check_cpu,
    check_disk,
    check_load,
    check_memory,
    check_network,
    check_thermal,
    run_checks,
)
from suite.cpu import CpuUsage, LoadAverage
from suite.disks import Disk
from suite.memory import Memory
from suite.network import NetCounters
from suite.profile import SystemStatsProfile
from suite.sampler import Sample
from suite.thermal import ThermalZone


def _cpu(percent: float) -> CpuUsage:
    return CpuUsage(overall_percent=percent, per_core_percent={"cpu0": percent})


def _disk(mount_point: str, free_percent: float) -> Disk:
    return Disk(
        device="/dev/fixture",
        filesystem="ext4",
        free_bytes=int(1000 * free_percent / 100),
        mount_point=mount_point,
        total_bytes=1000,
        used_bytes=1000 - int(1000 * free_percent / 100),
    )


def _memory(available_percent: float) -> Memory:
    return Memory(
        available_bytes=int(1000 * available_percent / 100),
        buffers_bytes=0,
        cached_bytes=0,
        free_bytes=0,
        swap_free_bytes=0,
        swap_total_bytes=0,
        total_bytes=1000,
    )


def _net(errors: int = 0, dropped: int = 0) -> NetCounters:
    return NetCounters(
        name="eth0",
        rx_bytes=0,
        rx_dropped=dropped,
        rx_errors=errors,
        rx_packets=0,
        tx_bytes=0,
        tx_dropped=0,
        tx_errors=0,
        tx_packets=0,
    )


def test_cpu_passes_at_the_ceiling(make_sample: Callable[..., Sample]) -> None:
    result = check_cpu(make_sample(cpu=_cpu(90.0)), ceiling_percent=90.0)

    assert result.status == "pass"


def test_cpu_fails_above_the_ceiling(make_sample: Callable[..., Sample]) -> None:
    result = check_cpu(make_sample(cpu=_cpu(90.1)), ceiling_percent=90.0)

    assert result.failed
    assert result.kind == "utilisation_above_ceiling"
    assert "90.1%" in result.reason
    assert result.detail["busiest_core"] == "cpu0"


def test_cpu_is_skipped_without_a_delta(make_sample: Callable[..., Sample]) -> None:
    result = check_cpu(make_sample(), ceiling_percent=90.0)

    assert result.status == "skip"
    assert not result.failed


def test_disk_passes_at_the_floor(make_sample: Callable[..., Sample]) -> None:
    result = check_disk(make_sample(disks=(_disk("/", 10.0),)), floor_free_percent=10.0)

    assert result.status == "pass"


def test_disk_fails_on_the_tightest_mount(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(disks=(_disk("/", 50.0), _disk("/data", 9.0)))

    result = check_disk(sample, floor_free_percent=10.0)

    assert result.failed
    assert result.kind == "free_space_below_floor"
    assert result.detail["mount_point"] == "/data"
    assert "/data" in result.reason


def test_disk_is_skipped_without_mounts(make_sample: Callable[..., Sample]) -> None:
    assert check_disk(make_sample(), floor_free_percent=10.0).status == "skip"


def test_load_passes_at_the_ceiling(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(cpu_count=4, load=LoadAverage(fifteen=1.0, five=1.0, one=8.0, runnable=1, total=100))

    assert check_load(sample, ceiling_per_core=2.0).status == "pass"


def test_load_fails_above_the_ceiling(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(cpu_count=4, load=LoadAverage(fifteen=1.0, five=1.0, one=8.4, runnable=1, total=100))

    result = check_load(sample, ceiling_per_core=2.0)

    assert result.failed
    assert result.kind == "load_above_ceiling"
    assert result.detail["load_per_core"] == 2.1


def test_load_is_skipped_without_a_reading(make_sample: Callable[..., Sample]) -> None:
    assert check_load(make_sample(), ceiling_per_core=2.0).status == "skip"


def test_load_is_skipped_when_no_cores_are_reported(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(cpu_count=0, load=LoadAverage(fifteen=1.0, five=1.0, one=1.0, runnable=1, total=10))

    assert check_load(sample, ceiling_per_core=2.0).status == "skip"


def test_memory_passes_at_the_floor(make_sample: Callable[..., Sample]) -> None:
    result = check_memory(make_sample(memory=_memory(10.0)), floor_available_percent=10.0)

    assert result.status == "pass"


def test_memory_fails_below_the_floor(make_sample: Callable[..., Sample]) -> None:
    result = check_memory(make_sample(memory=_memory(9.0)), floor_available_percent=10.0)

    assert result.failed
    assert result.kind == "available_below_floor"
    assert result.detail["available_percent"] == 9.0


def test_memory_is_skipped_without_a_reading(make_sample: Callable[..., Sample]) -> None:
    assert check_memory(make_sample(), floor_available_percent=10.0).status == "skip"


def test_network_passes_at_the_budget(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(network_growth={"eth0": _net(errors=1, dropped=1)})

    assert check_network(sample, max_new_errors=2).status == "pass"


def test_network_fails_above_the_budget(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(network_growth={"eth0": _net(errors=2, dropped=1)})

    result = check_network(sample, max_new_errors=2)

    assert result.failed
    assert result.kind == "counters_increased"
    assert result.detail["interface"] == "eth0"
    assert result.detail["new_errors"] == 2


def test_network_passes_when_nothing_grew(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(network_growth={"eth0": _net()})

    assert check_network(sample, max_new_errors=0).status == "pass"


def test_network_is_skipped_before_the_first_delta(make_sample: Callable[..., Sample]) -> None:
    assert check_network(make_sample(), max_new_errors=0).status == "skip"


def test_thermal_passes_at_the_ceiling(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(thermal=(ThermalZone(celsius=70.0, label="pkg", name="thermal_zone0"),))

    assert check_thermal(sample, ceiling_c=70.0).status == "pass"


def test_thermal_fails_on_the_hottest_zone(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(
        thermal=(
            ThermalZone(celsius=40.0, label="acpitz", name="thermal_zone0"),
            ThermalZone(celsius=70.5, label="pkg", name="thermal_zone1"),
        )
    )

    result = check_thermal(sample, ceiling_c=70.0)

    assert result.failed
    assert result.kind == "temperature_above_ceiling"
    assert result.detail["zone"] == "thermal_zone1"
    assert "pkg" in result.reason


def test_thermal_is_skipped_on_a_host_without_zones(make_sample: Callable[..., Sample]) -> None:
    assert check_thermal(make_sample(), ceiling_c=70.0).status == "skip"


def test_run_checks_covers_every_statistic_in_a_stable_order(make_sample: Callable[..., Sample]) -> None:
    results = run_checks(make_sample(), SystemStatsProfile())

    assert [result.name for result in results] == ["cpu", "disk", "load", "memory", "network", "thermal"]
    assert all(result.status == "skip" for result in results)


def test_run_checks_applies_the_profile_thresholds(make_sample: Callable[..., Sample]) -> None:
    profile = SystemStatsProfile(max_cpu_percent=50.0, min_available_memory_percent=50.0)
    sample = make_sample(cpu=_cpu(60.0), memory=_memory(40.0))

    failed = {result.name for result in run_checks(sample, profile) if result.failed}

    assert failed == {"cpu", "memory"}

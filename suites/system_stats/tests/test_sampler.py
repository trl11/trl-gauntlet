"""Sampler tests, against a fake ``/proc`` and ``/sys``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from suite.cpu import LoadAverage
from suite.disks import Disk
from suite.sampler import Sample, Sampler
from suite.thermal import ThermalZone

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_sample_derives_the_headline_readings(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(
        cpu_count=4,
        disks=(
            Disk(device="a", filesystem="ext4", free_bytes=800, mount_point="/", total_bytes=1000, used_bytes=200),
            Disk(device="b", filesystem="ext4", free_bytes=100, mount_point="/data", total_bytes=1000, used_bytes=900),
        ),
        load=LoadAverage(fifteen=1.0, five=1.0, one=2.0, runnable=1, total=10),
        thermal=(
            ThermalZone(celsius=40.0, label="a", name="thermal_zone0"),
            ThermalZone(celsius=61.0, label="b", name="thermal_zone1"),
        ),
    )

    assert sample.load_per_core == pytest.approx(0.5)
    assert sample.tightest_disk is not None
    assert sample.tightest_disk.mount_point == "/data"
    assert sample.hottest is not None
    assert sample.hottest.label == "b"


def test_sample_has_no_load_per_core_without_cores(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(cpu_count=0, load=LoadAverage(fifteen=1.0, five=1.0, one=2.0, runnable=1, total=10))

    assert sample.load_per_core is None


def test_sampler_reads_a_fixture_tree(proc_tree: Path, sys_tree: Path) -> None:
    sampler = Sampler(proc=proc_tree, sys_root=sys_tree, min_window_s=0.0)

    first = sampler.sample()

    assert first.cpu is None
    assert first.cpu_count == 4
    assert first.memory is not None
    assert first.load is not None
    assert first.process_count == 3
    assert first.procs_running == 3
    assert [zone.label for zone in first.thermal] == ["x86_pkg_temp", "acpitz"]
    assert set(first.network) == {"lo", "eth0"}
    assert len(first.disks) == 1

    (proc_tree / "stat").write_text((FIXTURES / "stat_after.txt").read_text())
    (proc_tree / "net" / "dev").write_text((FIXTURES / "net_dev_after.txt").read_text())
    second = sampler.sample()

    assert second.cpu is not None
    assert second.cpu.overall_percent == pytest.approx(29.41)
    assert second.context_switches_per_s is not None
    assert second.context_switches_per_s > 0
    assert second.network_growth["eth0"].errors == 5
    assert second.window_s > 0


def test_sampler_degrades_on_a_host_with_nothing_to_read(tmp_path: Path) -> None:
    sampler = Sampler(proc=tmp_path / "absent", sys_root=tmp_path / "absent", min_window_s=0.0)

    sample = sampler.sample()

    assert sample.cpu is None
    assert sample.context_switches_per_s is None
    assert sample.disks == ()
    assert sample.load is None
    assert sample.memory is None
    assert sample.network == {}
    assert sample.process_count is None
    assert sample.procs_running is None
    assert sample.thermal == ()
    assert sample.uptime_s is None
    assert sample.cpu_count > 0


def test_sampler_waits_for_a_measurable_window(proc_tree: Path, sys_tree: Path) -> None:
    sampler = Sampler(proc=proc_tree, sys_root=sys_tree, min_window_s=0.05)

    sampler.prime()
    sample = sampler.sample()

    assert sample.window_s >= 0.05

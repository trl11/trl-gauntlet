"""The ``/proc`` and ``/sys`` readers, against a kernel tree built by the test.

Reading the live host proves the readers do not raise; it cannot prove they
report the right numbers, because the right numbers change every second. Every
test here points them at files it wrote itself.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from gauntlet.api import host_stats

_STAT = textwrap.dedent(
    """\
    cpu  1000 20 300 8000 100 0 40 0 0 0
    cpu0 500 10 150 4000 50 0 20 0 0 0
    cpu1 500 10 150 4000 50 0 20 0 0 0
    intr 12345
    ctxt 987654
    btime 1767225600
    processes 4321
    """
)

_MEMINFO = textwrap.dedent(
    """\
    MemTotal:       16384000 kB
    MemFree:         2048000 kB
    MemAvailable:    8192000 kB
    SwapTotal:       4096000 kB
    SwapFree:        3072000 kB
    HugePages_Total:       0
    """
)

_CPUINFO = textwrap.dedent(
    """\
    processor	: 0
    vendor_id	: AuthenticAMD
    model name	: AMD Ryzen 9 5900X 12-Core Processor
    cpu MHz		: 3700.000
    """
)


@pytest.fixture
def proc(monkeypatch, tmp_path: Path) -> Path:
    """A ``/proc`` the readers are pointed at, holding the fixtures above."""
    root = tmp_path / "proc"
    root.mkdir()
    (root / "stat").write_text(_STAT)
    (root / "meminfo").write_text(_MEMINFO)
    (root / "cpuinfo").write_text(_CPUINFO)
    (root / "uptime").write_text("123456.78 987654.32\n")
    for pid in ("1", "42", "1337"):
        (root / pid).mkdir()
    (root / "self").mkdir()
    monkeypatch.setattr(host_stats, "_PROC", root)
    return root


@pytest.fixture
def thermal(monkeypatch, tmp_path: Path) -> Path:
    """A ``/sys/class/thermal`` with two readable zones and one that is not."""
    root = tmp_path / "thermal"
    root.mkdir()
    for name, label, millidegrees in (
        ("thermal_zone0", "x86_pkg_temp", "42500"),
        ("thermal_zone1", "acpitz", "51000"),
    ):
        zone = root / name
        zone.mkdir()
        (zone / "type").write_text(f"{label}\n")
        (zone / "temp").write_text(f"{millidegrees}\n")
    (root / "thermal_zone2").mkdir()
    (root / "cooling_device0").mkdir()
    monkeypatch.setattr(host_stats, "_THERMAL", root)
    return root


class TestBootTime:
    def test_reads_the_btime_line(self, proc: Path) -> None:
        assert host_stats.boot_time() == "2026-01-01T00:00:00Z"

    def test_a_non_numeric_btime_is_none(self, proc: Path) -> None:
        (proc / "stat").write_text("btime nonsense\n")
        assert host_stats.boot_time() is None

    def test_no_btime_line_is_none(self, proc: Path) -> None:
        (proc / "stat").write_text("cpu  1 2 3 4 5\n")
        assert host_stats.boot_time() is None


class TestCpuModel:
    def test_reads_the_model_name(self, proc: Path) -> None:
        assert host_stats.cpu_model() == "AMD Ryzen 9 5900X 12-Core Processor"

    def test_an_arm_style_label_is_read_too(self, proc: Path) -> None:
        (proc / "cpuinfo").write_text("Model\t: Raspberry Pi 5\n")
        assert host_stats.cpu_model() == "Raspberry Pi 5"

    def test_a_cpuinfo_without_a_name_is_none(self, proc: Path) -> None:
        (proc / "cpuinfo").write_text("processor\t: 0\nmodel name\t:\n")
        assert host_stats.cpu_model() is None


class TestCpuTimes:
    def test_idle_is_idle_plus_iowait_and_total_is_every_field(self, proc: Path) -> None:
        times = host_stats.cpu_times()
        assert times["cpu"] == (8100, 9460)
        assert set(times) == {"cpu", "cpu0", "cpu1"}

    def test_a_short_cpu_line_is_skipped(self, proc: Path) -> None:
        (proc / "stat").write_text("cpu  1 2 3\ncpu0 1 2 3 4 5 6\n")
        assert set(host_stats.cpu_times()) == {"cpu0"}

    def test_no_stat_file_reads_nothing(self, proc: Path) -> None:
        (proc / "stat").unlink()
        assert host_stats.cpu_times() == {}


class TestCpuPercent:
    def test_measures_the_busy_share_between_two_readings(self) -> None:
        before = {"cpu": (900, 1000), "cpu0": (900, 1000)}
        after = {"cpu": (1800, 2000), "cpu0": (1800, 2000)}
        assert host_stats.cpu_percent(before, after) == (10.0, [10.0])

    def test_the_first_reading_has_nothing_to_compare_against(self) -> None:
        assert host_stats.cpu_percent({}, {"cpu": (1, 2)}) == (None, [])
        assert host_stats.cpu_percent({"cpu": (1, 2)}, {}) == (None, [])

    def test_an_unchanged_reading_is_unmeasurable(self) -> None:
        same = {"cpu": (900, 1000)}
        assert host_stats.cpu_percent(same, same) == (None, [])

    def test_a_core_missing_from_the_earlier_reading_reads_zero(self) -> None:
        before = {"cpu": (900, 1000)}
        after = {"cpu": (1800, 2000), "cpu3": (1800, 2000)}
        assert host_stats.cpu_percent(before, after) == (10.0, [0.0])

    def test_cores_are_ordered_by_number_not_by_name(self) -> None:
        before = {f"cpu{n}": (0, 0) for n in (0, 2, 10)}
        after = {"cpu0": (10, 100), "cpu2": (20, 100), "cpu10": (30, 100)}
        assert host_stats.cpu_percent(before, after) == (None, [90.0, 80.0, 70.0])

    def test_an_unnumbered_core_line_still_sorts(self) -> None:
        before = {"cpu_odd": (0, 0), "cpu1": (0, 0)}
        after = {"cpu_odd": (10, 100), "cpu1": (20, 100)}
        assert host_stats.cpu_percent(before, after) == (None, [90.0, 80.0])


class TestMemory:
    def test_used_is_total_less_available(self, proc: Path) -> None:
        assert host_stats.memory() == {
            "total": 16_777_216_000,
            "available": 8_388_608_000,
            "used": 8_388_608_000,
            "percent": 50.0,
        }

    def test_swap_is_total_less_free(self, proc: Path) -> None:
        assert host_stats.swap() == {"total": 4_194_304_000, "used": 1_048_576_000, "percent": 25.0}

    def test_a_meminfo_without_the_keys_reports_nulls(self, proc: Path) -> None:
        (proc / "meminfo").write_text("Buffers: 100 kB\n")
        assert host_stats.memory() == {"total": None, "available": None, "used": None, "percent": None}
        assert host_stats.swap() == {"total": None, "used": None, "percent": None}

    def test_a_value_without_a_unit_is_taken_as_bytes(self, proc: Path) -> None:
        (proc / "meminfo").write_text("HugePages_Total: 12\n")
        assert host_stats.meminfo() == {"HugePages_Total": 12}

    def test_an_unparsable_line_is_skipped(self, proc: Path) -> None:
        (proc / "meminfo").write_text("MemTotal: lots kB\nMemFree:\nMemAvailable: 100 kB\n")
        assert host_stats.meminfo() == {"MemAvailable": 102_400}


class TestDisks:
    def test_reports_a_mount_backed_by_real_storage(self, proc: Path, tmp_path: Path) -> None:
        (proc / "mounts").write_text(f"/dev/sda1 {tmp_path} ext4 rw 0 0\nproc /proc proc rw 0 0\n")
        assert [disk["mount"] for disk in host_stats.disks()] == [str(tmp_path)]

    def test_a_mount_named_twice_is_reported_once(self, proc: Path, tmp_path: Path) -> None:
        line = f"/dev/sda1 {tmp_path} ext4 rw 0 0\n"
        (proc / "mounts").write_text(line * 2)
        assert len(host_stats.disks()) == 1

    def test_a_bind_mounted_file_is_not_a_disk(self, proc: Path, tmp_path: Path) -> None:
        bound = tmp_path / "hostname"
        bound.write_text("host\n")
        (proc / "mounts").write_text(f"/dev/sda1 {bound} ext4 rw 0 0\n")
        # Nothing was mounted, so the root filesystem stands in.
        assert [disk["mount"] for disk in host_stats.disks()] == ["/"]

    def test_a_space_in_a_mount_point_is_unescaped(self, proc: Path, tmp_path: Path) -> None:
        spaced = tmp_path / "my disk"
        spaced.mkdir()
        (proc / "mounts").write_text(f"/dev/sda1 {str(spaced).replace(' ', chr(92) + '040')} ext4 rw 0 0\n")
        assert [disk["mount"] for disk in host_stats.disks()] == [str(spaced)]

    def test_a_mount_that_cannot_be_measured_is_dropped(self, monkeypatch, proc: Path, tmp_path: Path) -> None:
        (proc / "mounts").write_text(f"/dev/sda1 {tmp_path} ext4 rw 0 0\n")
        monkeypatch.setattr(host_stats.shutil, "disk_usage", _raise_oserror)
        assert host_stats.disks() == []

    def test_the_percentage_is_the_used_share_of_the_volume(self, proc: Path, tmp_path: Path) -> None:
        (proc / "mounts").write_text(f"/dev/sda1 {tmp_path} ext4 rw 0 0\n")
        disk = host_stats.disks()[0]
        assert disk["percent"] == pytest.approx(100.0 * disk["used"] / disk["total"], abs=0.05)


class TestLoadAvg:
    def test_reports_three_figures(self) -> None:
        assert len(host_stats.load_avg() or []) == 3

    def test_a_kernel_that_refuses_reports_none(self, monkeypatch) -> None:
        monkeypatch.setattr(host_stats.os, "getloadavg", _raise_oserror)
        assert host_stats.load_avg() is None


class TestProcessCount:
    def test_counts_only_the_numeric_entries(self, proc: Path) -> None:
        assert host_stats.process_count() == 3

    def test_no_proc_reports_none(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(host_stats, "_PROC", tmp_path / "absent")
        assert host_stats.process_count() is None


class TestUptime:
    def test_reads_the_first_figure(self, proc: Path) -> None:
        assert host_stats.uptime() == 123456.8

    def test_an_unparsable_uptime_is_none(self, proc: Path) -> None:
        (proc / "uptime").write_text("forever ever\n")
        assert host_stats.uptime() is None

    def test_an_empty_uptime_is_none(self, proc: Path) -> None:
        (proc / "uptime").write_text("")
        assert host_stats.uptime() is None


class TestTemperatures:
    def test_reports_each_readable_zone_with_its_label(self, thermal: Path) -> None:
        assert host_stats.temperatures() == [
            {"label": "x86_pkg_temp", "celsius": 42.5},
            {"label": "acpitz", "celsius": 51.0},
        ]

    def test_a_zone_without_a_reading_is_skipped(self, thermal: Path) -> None:
        assert "thermal_zone2" not in [reading["label"] for reading in host_stats.temperatures()]

    def test_a_zone_without_a_type_is_named_after_its_directory(self, thermal: Path) -> None:
        (thermal / "thermal_zone0" / "type").unlink()
        assert host_stats.temperatures()[0]["label"] == "thermal_zone0"

    def test_no_thermal_directory_reports_nothing(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(host_stats, "_THERMAL", tmp_path / "absent")
        assert host_stats.temperatures() == []

    def test_an_unreadable_thermal_directory_reports_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(host_stats, "_thermal_zones", lambda: [])
        assert host_stats.temperatures() == []


class TestStaticInfo:
    def test_carries_the_versions_it_was_given(self, proc: Path) -> None:
        info = host_stats.static_info("9.9.9", "3.12.0")
        assert info["gauntlet"] == "9.9.9"
        assert info["python"] == "3.12.0"
        assert info["cpu_model"] == "AMD Ryzen 9 5900X 12-Core Processor"
        assert info["memory_total_bytes"] == 16_777_216_000
        assert info["boot_time"] == "2026-01-01T00:00:00Z"


def _raise_oserror(*args: object, **kwargs: object) -> None:
    raise OSError("no")

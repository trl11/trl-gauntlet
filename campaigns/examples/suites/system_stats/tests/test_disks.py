"""Mount parsing and disk usage.

``read_disks`` calls ``statvfs`` for real, so these tests build mount lines
that name directories and files inside ``tmp_path``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from suite.disks import parse_mounts, read_disks, statvfs_usage


def test_parse_mounts_drops_pseudo_filesystems_and_unescapes(fixture_text: Callable[[str], str]) -> None:
    mounts = parse_mounts(fixture_text("mounts.txt"))

    assert [mount.mount_point for mount in mounts] == [
        "/",
        "/workspaces/gauntlet",
        "/etc/hosts",
        "/home/dev/.ssh",
        "/media/My Disk",
    ]
    assert mounts[0].filesystem == "overlay"
    assert mounts[1].device == "/dev/mapper/home_crypt"


def test_parse_mounts_ignores_a_truncated_line() -> None:
    assert parse_mounts("/dev/sda1 /data\n") == ()


def test_statvfs_usage_measures_a_real_directory(tmp_path: Path) -> None:
    usage = statvfs_usage(str(tmp_path))

    assert usage is not None
    total, used, free = usage
    assert total > 0
    assert used >= 0
    assert free <= total


def test_statvfs_usage_returns_none_for_a_path_that_does_not_exist(tmp_path: Path) -> None:
    assert statvfs_usage(str(tmp_path / "absent")) is None


def test_read_disks_measures_each_mounted_directory(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (tmp_path / "mounts").write_text(f"/dev/one {first} ext4 rw,relatime 0 0\n/dev/two {second} ext4 rw,relatime 0 0\n")

    disks = read_disks(proc=tmp_path)

    assert [disk.mount_point for disk in disks] == [str(first), str(second)]
    assert disks[0].filesystem == "ext4"
    assert disks[0].free_percent == pytest.approx(100.0 * disks[0].free_bytes / disks[0].total_bytes)


def test_read_disks_keeps_only_the_first_mount_of_a_device(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (tmp_path / "mounts").write_text(f"/dev/one {first} ext4 rw,relatime 0 0\n/dev/one {second} ext4 rw,relatime 0 0\n")

    assert [disk.mount_point for disk in read_disks(proc=tmp_path)] == [str(first)]


def test_read_disks_skips_a_mount_point_that_is_a_file(tmp_path: Path) -> None:
    bound = tmp_path / "hosts"
    bound.write_text("127.0.0.1 localhost\n")
    (tmp_path / "mounts").write_text(f"/dev/one {bound} ext4 rw,relatime 0 0\n")

    assert read_disks(proc=tmp_path) == ()


def test_read_disks_skips_a_mount_point_that_does_not_exist(tmp_path: Path) -> None:
    (tmp_path / "mounts").write_text(f"/dev/one {tmp_path}/absent ext4 rw,relatime 0 0\n")

    assert read_disks(proc=tmp_path) == ()


def test_read_disks_skips_pseudo_filesystems(tmp_path: Path) -> None:
    (tmp_path / "mounts").write_text(f"tmpfs {tmp_path} tmpfs rw 0 0\nproc {tmp_path} proc rw 0 0\n")

    assert read_disks(proc=tmp_path) == ()


def test_read_disks_is_empty_when_its_source_is_missing(tmp_path: Path) -> None:
    assert read_disks(proc=tmp_path / "absent") == ()

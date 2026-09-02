"""Memory reader, against captured ``/proc/meminfo`` text."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from suite.memory import parse_meminfo, read_meminfo

KIB = 1024


def test_parse_meminfo(fixture_text: Callable[[str], str]) -> None:
    memory = parse_meminfo(fixture_text("meminfo.txt"))

    assert memory is not None
    assert memory.total_bytes == 65746608 * KIB
    assert memory.available_bytes == 46367720 * KIB
    assert memory.swap_used_bytes == (2097148 - 798372) * KIB
    assert memory.available_percent == pytest.approx(70.5, abs=0.1)
    assert memory.used_percent == pytest.approx(29.5, abs=0.1)


def test_parse_meminfo_falls_back_when_mem_available_is_absent() -> None:
    memory = parse_meminfo("MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 150 kB\n")

    assert memory is not None
    assert memory.available_bytes == 300 * KIB


def test_parse_meminfo_returns_none_without_a_total() -> None:
    assert parse_meminfo("MemFree: 100 kB\n") is None


def test_parse_meminfo_ignores_a_line_without_a_number() -> None:
    memory = parse_meminfo("MemTotal: 1000 kB\nHugePagesize: kB\n")

    assert memory is not None
    assert memory.total_bytes == 1000 * KIB


def test_memory_percentages_are_zero_when_nothing_is_installed() -> None:
    memory = parse_meminfo("MemTotal: 0 kB\nMemFree: 0 kB\n")

    assert memory is not None
    assert memory.available_percent == 0.0
    assert memory.swap_used_percent == 0.0


def test_read_meminfo_reads_the_fixture_tree(proc_tree: Path) -> None:
    assert read_meminfo(proc=proc_tree) is not None


def test_read_meminfo_returns_none_when_its_source_is_missing(tmp_path: Path) -> None:
    assert read_meminfo(proc=tmp_path / "absent") is None


def test_read_meminfo_returns_none_when_its_source_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "meminfo").mkdir()

    assert read_meminfo(proc=tmp_path) is None

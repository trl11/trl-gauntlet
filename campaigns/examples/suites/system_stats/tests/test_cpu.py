"""CPU and load-average readers, against captured ``/proc`` text."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from suite.cpu import cpu_usage, parse_loadavg, parse_stat, read_loadavg, read_stat


def test_parse_stat_reads_cpu_lines_and_counters(fixture_text: Callable[[str], str]) -> None:
    stat = parse_stat(fixture_text("stat.txt"))

    assert stat is not None
    assert [times.name for times in stat.per_core] == ["cpu0", "cpu1", "cpu2", "cpu3"]
    assert stat.overall.idle == 11622646505 + 28547320
    assert stat.context_switches == 140006659605
    assert stat.forks == 164429122
    assert stat.procs_blocked == 1
    assert stat.procs_running == 3


def test_parse_stat_returns_none_without_a_cpu_line() -> None:
    assert parse_stat("ctxt 12\nprocs_running 1\n") is None


def test_parse_stat_ignores_a_truncated_cpu_line() -> None:
    stat = parse_stat("cpu  1 2 3 4 5\ncpu0 1 2\n")

    assert stat is not None
    assert stat.per_core == ()


def test_parse_stat_ignores_a_cpu_line_with_a_non_numeric_column() -> None:
    assert parse_stat("cpu  1 2 3 4 five\n") is None


def test_cpu_usage_reports_the_busy_share(fixture_text: Callable[[str], str]) -> None:
    before = parse_stat(fixture_text("stat.txt"))
    after = parse_stat(fixture_text("stat_after.txt"))
    assert before is not None and after is not None

    usage = cpu_usage(before, after)

    assert usage is not None
    assert usage.overall_percent == pytest.approx(29.41)
    assert usage.per_core_percent == {"cpu0": 75.0, "cpu1": 0.0, "cpu2": 50.0, "cpu3": 0.0}


def test_cpu_usage_returns_none_when_no_jiffies_elapsed(fixture_text: Callable[[str], str]) -> None:
    stat = parse_stat(fixture_text("stat.txt"))
    assert stat is not None

    assert cpu_usage(stat, stat) is None


def test_cpu_usage_skips_cores_that_appeared_since_the_previous_read() -> None:
    before = parse_stat("cpu  10 0 10 100 0 0 0\ncpu0 10 0 10 100 0 0 0\n")
    after = parse_stat("cpu  20 0 10 200 0 0 0\ncpu0 20 0 10 200 0 0 0\ncpu1 5 0 5 50 0 0 0\n")
    assert before is not None and after is not None

    usage = cpu_usage(before, after)

    assert usage is not None
    assert set(usage.per_core_percent) == {"cpu0"}


def test_parse_loadavg(fixture_text: Callable[[str], str]) -> None:
    load = parse_loadavg(fixture_text("loadavg.txt"))

    assert load is not None
    assert (load.one, load.five, load.fifteen) == (3.29, 2.96, 3.0)
    assert (load.runnable, load.total) == (3, 2131)


@pytest.mark.parametrize("text", ["", "3.29 2.96\n", "a b c 1/2 3\n"])
def test_parse_loadavg_returns_none_when_unusable(text: str) -> None:
    assert parse_loadavg(text) is None


def test_readers_read_the_fixture_tree(proc_tree: Path) -> None:
    assert read_stat(proc=proc_tree) is not None
    assert read_loadavg(proc=proc_tree) is not None


def test_readers_return_none_when_their_source_is_missing(tmp_path: Path) -> None:
    absent = tmp_path / "absent"

    assert read_stat(proc=absent) is None
    assert read_loadavg(proc=absent) is None


def test_readers_return_none_when_their_source_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "stat").mkdir()
    (tmp_path / "loadavg").mkdir()

    assert read_stat(proc=tmp_path) is None
    assert read_loadavg(proc=tmp_path) is None

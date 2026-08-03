"""Processor name, uptime and process count."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from suite.host import parse_cpu_model, parse_uptime, read_cpu_model, read_process_count, read_uptime


def test_parse_cpu_model(fixture_text: Callable[[str], str]) -> None:
    assert parse_cpu_model(fixture_text("cpuinfo.txt")) == "AMD Ryzen 9 5900X 12-Core Processor"


def test_parse_cpu_model_accepts_the_arm_key() -> None:
    assert parse_cpu_model("processor\t: 0\nModel\t: Raspberry Pi 5\n") == "Raspberry Pi 5"


def test_parse_cpu_model_returns_none_when_unnamed() -> None:
    assert parse_cpu_model("processor\t: 0\nflags\t\t: fpu\n") is None


def test_parse_uptime(fixture_text: Callable[[str], str]) -> None:
    assert parse_uptime(fixture_text("uptime.txt")) == pytest.approx(5402644.89)


@pytest.mark.parametrize("text", ["", "not-a-number 1\n"])
def test_parse_uptime_returns_none_when_unusable(text: str) -> None:
    assert parse_uptime(text) is None


def test_read_process_count_counts_pids(proc_tree: Path) -> None:
    assert read_process_count(proc=proc_tree) == 3


def test_readers_read_the_fixture_tree(proc_tree: Path) -> None:
    assert read_cpu_model(proc=proc_tree) == "AMD Ryzen 9 5900X 12-Core Processor"
    assert read_uptime(proc=proc_tree) is not None


def test_readers_return_none_when_their_source_is_missing(tmp_path: Path) -> None:
    absent = tmp_path / "absent"

    assert read_cpu_model(proc=absent) is None
    assert read_process_count(proc=absent) is None
    assert read_uptime(proc=absent) is None

"""Interface counters, against captured ``/proc/net/dev`` text."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from suite.network import NetCounters, net_deltas, parse_net_dev, read_net_dev


def _counters(**overrides: int) -> NetCounters:
    fields: dict[str, int] = {
        "rx_bytes": 0,
        "rx_dropped": 0,
        "rx_errors": 0,
        "rx_packets": 0,
        "tx_bytes": 0,
        "tx_dropped": 0,
        "tx_errors": 0,
        "tx_packets": 0,
    }
    return NetCounters(name="eth0", **{**fields, **overrides})


def test_parse_net_dev(fixture_text: Callable[[str], str]) -> None:
    interfaces = parse_net_dev(fixture_text("net_dev.txt"))

    assert set(interfaces) == {"lo", "eth0"}
    assert interfaces["eth0"].rx_bytes == 4469006923
    assert interfaces["eth0"].rx_packets == 1130459
    assert interfaces["eth0"].tx_bytes == 83971141
    assert interfaces["eth0"].errors == 0
    assert interfaces["eth0"].dropped == 0


def test_parse_net_dev_ignores_the_header_and_short_rows() -> None:
    assert parse_net_dev("Inter-|   Receive\n eth0: 1 2 3\n") == {}


def test_parse_net_dev_ignores_a_row_with_a_non_numeric_column() -> None:
    row = " eth0: 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 sixteen\n"

    assert parse_net_dev(row) == {}


def test_net_deltas_reports_growth(fixture_text: Callable[[str], str]) -> None:
    before = parse_net_dev(fixture_text("net_dev.txt"))
    after = parse_net_dev(fixture_text("net_dev_after.txt"))

    deltas = net_deltas(before, after)

    assert set(deltas) == {"lo", "eth0"}
    assert deltas["eth0"].rx_bytes == 2000
    assert deltas["eth0"].errors == 5
    assert deltas["eth0"].dropped == 1


def test_net_deltas_clamps_a_counter_that_went_backwards() -> None:
    before = {"eth0": _counters(rx_bytes=500, rx_dropped=4, rx_errors=3, rx_packets=20, tx_bytes=500)}
    after = {"eth0": _counters(rx_bytes=10, rx_packets=1, tx_bytes=10, tx_packets=1)}

    deltas = net_deltas(before, after)

    assert deltas["eth0"].rx_bytes == 0
    assert deltas["eth0"].errors == 0


def test_read_net_dev_reads_the_fixture_tree(proc_tree: Path) -> None:
    assert set(read_net_dev(proc=proc_tree)) == {"lo", "eth0"}


def test_read_net_dev_is_empty_when_its_source_is_missing(tmp_path: Path) -> None:
    assert read_net_dev(proc=tmp_path / "absent") == {}

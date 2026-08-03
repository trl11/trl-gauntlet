"""Per-interface counters, from ``/proc/net/dev``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from suite.procfs import PROC, parse_ints, read_text

# Columns per interface: eight receive fields then eight transmit fields.
_COLUMNS = 16


@dataclass(frozen=True)
class NetCounters:
    """One interface's row of ``/proc/net/dev``."""

    name: str
    rx_bytes: int
    rx_dropped: int
    rx_errors: int
    rx_packets: int
    tx_bytes: int
    tx_dropped: int
    tx_errors: int
    tx_packets: int

    @property
    def dropped(self) -> int:
        return self.rx_dropped + self.tx_dropped

    @property
    def errors(self) -> int:
        return self.rx_errors + self.tx_errors


def net_deltas(previous: dict[str, NetCounters], current: dict[str, NetCounters]) -> dict[str, NetCounters]:
    """Per-interface counter growth between two reads.

    Interfaces that appeared since the previous read are omitted, and a counter
    that went backwards — a reset, or a driver reload — contributes zero rather
    than a negative delta.
    """
    deltas: dict[str, NetCounters] = {}
    for name, now in current.items():
        earlier = previous.get(name)
        if earlier is None:
            continue
        deltas[name] = NetCounters(
            name=name,
            rx_bytes=max(0, now.rx_bytes - earlier.rx_bytes),
            rx_dropped=max(0, now.rx_dropped - earlier.rx_dropped),
            rx_errors=max(0, now.rx_errors - earlier.rx_errors),
            rx_packets=max(0, now.rx_packets - earlier.rx_packets),
            tx_bytes=max(0, now.tx_bytes - earlier.tx_bytes),
            tx_dropped=max(0, now.tx_dropped - earlier.tx_dropped),
            tx_errors=max(0, now.tx_errors - earlier.tx_errors),
            tx_packets=max(0, now.tx_packets - earlier.tx_packets),
        )
    return deltas


def parse_net_dev(text: str) -> dict[str, NetCounters]:
    """Per-interface counters. The two header lines carry no colon and are skipped."""
    interfaces: dict[str, NetCounters] = {}
    for line in text.splitlines():
        name, separator, rest = line.partition(":")
        if not separator:
            continue
        name = name.strip()
        fields = rest.split()
        numbers = parse_ints(fields[:_COLUMNS]) if len(fields) >= _COLUMNS else None
        if not name or numbers is None:
            continue
        interfaces[name] = NetCounters(
            name=name,
            rx_bytes=numbers[0],
            rx_packets=numbers[1],
            rx_errors=numbers[2],
            rx_dropped=numbers[3],
            tx_bytes=numbers[8],
            tx_packets=numbers[9],
            tx_errors=numbers[10],
            tx_dropped=numbers[11],
        )
    return interfaces


def read_net_dev(*, proc: Path = PROC) -> dict[str, NetCounters]:
    """Interface counters, empty when ``/proc/net/dev`` is unreadable."""
    text = read_text(proc / "net" / "dev")
    return parse_net_dev(text) if text is not None else {}

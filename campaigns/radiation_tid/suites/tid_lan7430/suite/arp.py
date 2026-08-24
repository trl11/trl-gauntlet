"""Making the unit answer ARP only on the interface that owns the address.

A host with two interfaces on one subnet answers ARP for either address out of
either interface, so the switch can learn the part's address against the other
NIC. Traffic addressed to the part then arrives on the built-in interface,
which Linux accepts, while replies still leave over the part because its route
wins. Half the measurement is made on the wrong hardware, and iperf3 reports a
healthy symmetric gigabit either way.

``arp_ignore=1`` answers only for an address the receiving interface holds.
``arp_announce=2`` picks the source of an outgoing request from the interface
it leaves by. The kernel takes the larger of the ``all`` and per-interface
values for both, so setting ``all`` covers every interface at once.

Flushing the neighbour table afterwards matters as much as the settings: until
the entry the switch already holds is replaced, traffic keeps arriving where it
was arriving before.
"""

from __future__ import annotations

from typing import Any

from gauntlet_sdk.remote import RemoteError, run

STRICT = {"arp_announce": 2, "arp_ignore": 1}

SETTINGS = ("arp_announce", "arp_ignore")


def read(client: Any, *, timeout: float) -> dict[str, int]:
    """The unit's current ARP settings.

    Missing or unreadable values come back absent rather than as a guess, so a
    caller can tell "not strict" from "could not be read".
    """
    paths = " ".join(f"/proc/sys/net/ipv4/conf/all/{name}" for name in SETTINGS)
    result = run(client, f"cat {paths}", timeout=timeout)
    if not result.ok:
        return {}
    values = result.stdout.split()
    if len(values) != len(SETTINGS):
        return {}
    settings: dict[str, int] = {}
    for name, value in zip(SETTINGS, values, strict=True):
        try:
            settings[name] = int(value)
        except ValueError:
            return {}
    return settings


def is_strict(settings: dict[str, int]) -> bool:
    """Do these settings already keep each interface to its own addresses."""
    return bool(settings) and all(settings.get(name, -1) >= wanted for name, wanted in STRICT.items())


def enforce(client: Any, *, timeout: float) -> dict[str, int]:
    """Set strict ARP on the unit and drop the neighbour entries it invalidates.

    Returns the settings as they read back afterwards, so a caller reports what
    the unit is actually doing rather than what it was asked to do.
    """
    assignments = " ".join(f"net.ipv4.conf.all.{name}={value}" for name, value in sorted(STRICT.items()))
    result = run(client, f"sudo -n sysctl -w {assignments}", timeout=timeout)
    if not result.ok:
        raise RemoteError(f"setting strict ARP on the unit: {result.output}")
    run(client, "sudo -n ip neigh flush all", timeout=timeout)
    return read(client, timeout=timeout)


def describe(settings: dict[str, int]) -> str:
    """The settings as one readable clause for the run log."""
    if not settings:
        return "unreadable"
    return ", ".join(f"{name}={settings[name]}" for name in SETTINGS if name in settings)

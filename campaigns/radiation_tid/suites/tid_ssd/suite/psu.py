"""Bench supply readings, recorded beside the throughput.

Supply current climbing with accumulated dose is one of the clearest total
ionising dose signatures a part gives, so it is worth logging whenever the
bench has a supply on the rail.

This reads and never commands, and it does not appear in the suite's
``requires:``. Two things follow from that. Gauntlet does not reserve the
supply for the run, so the operator keeps control of the rail while the beam
is on, which is what they need during an exposure. And a bench with no supply
registered costs the run nothing: the reader reports itself unavailable and
every tick carries on without it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

# Readings worth carrying every tick. The supply reports its setpoints too,
# which do not change on their own and belong in the run summary instead.
READING_NAMES = ("current", "power", "voltage")


class PsuReader:
    """Read-only view of a ``psu`` capability, if the bench has one."""

    def __init__(self, url: str, *, timeout_s: float = 5.0) -> None:
        self._timeout_s = timeout_s
        self._url = url

    @classmethod
    def discover(cls, api_base: str | None, capability: str, *, timeout_s: float = 5.0) -> PsuReader | None:
        """Build a reader for the running Gauntlet, or None when there is none.

        Answers None rather than raising when the capability is absent, so a
        bench without a supply needs no different profile.
        """
        if not api_base:
            return None
        reader = cls(f"{api_base.rstrip('/')}/capabilities/{capability}", timeout_s=timeout_s)
        return reader if reader.read() is not None else None

    def read(self) -> dict[str, float] | None:
        """One reading, or None if the supply could not be reached.

        A value the supply reports as null — which is what it answers while it
        is unreachable — is left out rather than recorded as zero.
        """
        request = urllib.request.Request(self._url, headers={"accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                payload = json.load(reply)
        except (urllib.error.URLError, OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return {name: float(payload[name]) for name in READING_NAMES if isinstance(payload.get(name), (int, float))}

    def describe(self) -> dict[str, Any]:
        """What the supply is set to, for the run summary."""
        request = urllib.request.Request(self._url, headers={"accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                payload = json.load(reply)
        except (urllib.error.URLError, OSError, ValueError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

"""A DAQ module's analog inputs, captured beside the bandwidth.

A rail or a shunt riding on the beam line is another way total ionising dose
shows up, sample by sample rather than as a mean, so it is worth capturing
whenever the bench has a module.

This never reserves the module and does not appear in the suite's
``requires:``. Two things follow from that, the same as for the bench supply:
a bench with no module registered costs the run nothing, and nothing here
locks the operator's panel while a capture is in flight. Unlike the supply,
a capture is a command, not a read, but it is the one command that does not
touch how the module is configured: this suite does not know what is wired to
which channel, so it only ever asks for a window of whatever the module is
already set to and lets the sample say which channel it names.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class DaqReader:
    """Capturing view of a ``daq`` capability, if the bench has one."""

    def __init__(self, url: str, *, timeout_s: float = 15.0) -> None:
        self._timeout_s = timeout_s
        self._url = url

    @classmethod
    def discover(cls, api_base: str | None, capability: str, *, timeout_s: float = 15.0) -> DaqReader | None:
        """Build a reader for the running Gauntlet, or None when there is none.

        Answers None rather than raising when the capability is absent, so a
        bench without a module needs no different profile.
        """
        if not api_base:
            return None
        reader = cls(f"{api_base.rstrip('/')}/capabilities/{capability}", timeout_s=timeout_s)
        return reader if reader._state() is not None else None

    def capture(self, rate_hz: float, samples: int) -> dict[str, Any] | None:
        """One window of samples from every channel, or None if the module could not be reached."""
        try:
            return self._post({"command": "capture", "args": {"rate_hz": rate_hz, "samples": samples}})
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def _state(self) -> dict[str, Any] | None:
        request = urllib.request.Request(self._url, headers={"accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                payload = json.load(reply)
        except (urllib.error.URLError, OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
            return dict(json.load(reply))

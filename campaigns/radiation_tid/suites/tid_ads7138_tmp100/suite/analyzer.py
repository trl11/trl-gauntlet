"""The logic analyzer, as this suite reaches it.

Gauntlet owns the board. A suite naming ``logic`` in ``requires:`` is granted
a URL and drives it over HTTP, which is why nothing here opens a USB device.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from suite.adc import GPO_VALUE


class AnalyzerError(RuntimeError):
    """The instrument refused a capture, or could not be reached."""


def levels_and_edges(channels: dict[str, dict[str, Any]]) -> dict[int, tuple[int, int]]:
    """What each probe sat at and how often it changed, by probe number."""
    return {
        int(probe): (int(reading.get("level") or 0), int(reading.get("edges") or 0))
        for probe, reading in channels.items()
    }


class Analyzer:
    """One granted ``logic`` capability."""

    def __init__(self, url: str, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s
        self._url = url

    def capture(self, rate: str, window: str) -> dict[int, tuple[int, int]]:
        """Take one window of samples and return each probe's level and edge count."""
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"command": "capture", "args": {"rate": rate, "window": window}}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                payload = dict(json.load(reply))
        except urllib.error.HTTPError as exc:
            raise AnalyzerError(f"capture: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise AnalyzerError(f"capture: {exc}") from exc
        return levels_and_edges(payload.get("channels") or {})


class MockAnalyzer:
    """The probes as the mock part is driving them, for a run with no bench."""

    def __init__(self, adc: Any, probes: dict[int, int]) -> None:
        self._adc = adc
        self._probes = probes

    def capture(self, rate: str, window: str) -> dict[int, tuple[int, int]]:
        """The level each output is holding, steady for the whole window."""
        driven = self._adc.read_register(GPO_VALUE)
        return {probe: ((driven >> channel) & 1, 0) for channel, probe in self._probes.items()}


def _detail(error: urllib.error.HTTPError) -> str:
    """What the instrument said, for a capture it explained."""
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"

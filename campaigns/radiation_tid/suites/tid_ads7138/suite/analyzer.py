"""The logic analyzer, as this suite reaches it.

Gauntlet owns the board. A suite naming ``logic`` in ``requires:`` is granted
a URL and drives it over HTTP, which is why nothing here opens a USB device.

``urllib`` rather than a client library, because the SDK depends on pydantic
and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

from suite.adc import GPI_VALUE

# What a mock capture reports, so a run with no bench writes a trace of the
# same shape a real one does.
_MOCK_RATE_HZ = 1_000_000
_MOCK_SAMPLES = 1000


class AnalyzerError(RuntimeError):
    """The instrument refused a capture, or could not be reached."""


class Capture:
    """One window of samples, as the instrument reported it."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.channels: dict[str, dict[str, Any]] = payload.get("channels") or {}
        self.rate_hz: int = int(payload.get("rate_hz") or 0)
        self.samples: int = int(payload.get("samples") or 0)
        self._samples: str = str(payload.get("samples_base64") or "")

    @property
    def samples_base64(self) -> str:
        """Every sample as it arrived: one byte each, bit *n* being probe *n+1*."""
        return self._samples

    def levels(self) -> dict[int, int]:
        """What each probe was sitting at, by probe number."""
        return {int(probe): int(reading.get("level") or 0) for probe, reading in self.channels.items()}


class Analyzer:
    """One granted ``logic`` capability."""

    def __init__(self, url: str, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s
        self._url = url

    def capture(self, rate: str, window: str) -> Capture:
        """Take one window of samples and return what every probe did in it."""
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"command": "capture", "args": {"rate": rate, "window": window}}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                return Capture(dict(json.load(reply)))
        except urllib.error.HTTPError as exc:
            raise AnalyzerError(f"capture: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise AnalyzerError(f"capture: {exc}") from exc


class MockAnalyzer:
    """The probes as the mock part is driving them, for a run with no bench.

    It reads the part it was given rather than synthesising a waveform, so a
    mock run exercises the same comparison a real one does.
    """

    def __init__(self, adc: Any, probe_map: list[int]) -> None:
        self._adc = adc
        self._probe_map = probe_map

    def capture(self, rate: str, window: str) -> Capture:
        """The levels the part is holding, reported as a capture."""
        driven = self._adc.read_register(GPI_VALUE)
        channels = {str(probe): {"level": (driven >> output) & 1} for output, probe in enumerate(self._probe_map)}
        held = 0
        for output, probe in enumerate(self._probe_map):
            held |= ((driven >> output) & 1) << (probe - 1)
        samples = bytes([held]) * _MOCK_SAMPLES
        return Capture(
            {
                "channels": channels,
                "rate_hz": _MOCK_RATE_HZ,
                "samples": len(samples),
                "samples_base64": base64.b64encode(samples).decode(),
            }
        )


def _detail(error: urllib.error.HTTPError) -> str:
    """What the instrument said, for a capture it explained.

    A rejected command answers 422 carrying the provider's own words, which is
    the difference between "rate must be one of ..." and "HTTP 422".
    """
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"

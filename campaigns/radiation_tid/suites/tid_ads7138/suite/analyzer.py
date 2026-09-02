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


class AnalyzerError(RuntimeError):
    """The instrument refused a capture, or could not be reached."""


class Capture:
    """One window of samples, as the instrument reported it."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.channels: dict[str, dict[str, Any]] = payload.get("channels") or {}
        self.samples: int = int(payload.get("samples") or 0)
        self._image: str = str(payload.get("image_base64") or "")

    @property
    def image(self) -> bytes:
        """The picture of the capture, empty when it answered without one."""
        if not self._image:
            return b""
        try:
            return base64.b64decode(self._image)
        except ValueError as exc:
            raise AnalyzerError(f"capture: the picture would not decode: {exc}") from exc

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
        return Capture({"channels": channels, "samples": 0})


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

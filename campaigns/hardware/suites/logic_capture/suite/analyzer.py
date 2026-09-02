"""The logic analyzer, as this suite reaches it.

Gauntlet owns the board. A suite naming ``logic`` in ``requires:`` is granted
a URL and drives it over HTTP, which is why nothing here opens a USB device or
knows what an FX2 is: another eight-channel analyzer behind the same
capability would need no change.

``urllib`` rather than a client library, because the SDK depends on pydantic
and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any


class AnalyzerError(RuntimeError):
    """The instrument refused a command, or could not be reached."""


class Capture:
    """One window of samples, as the instrument reported it."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.channels: dict[str, dict[str, Any]] = payload.get("channels") or {}
        self.rate_hz: int = int(payload.get("rate_hz") or 0)
        self.samples: int = int(payload.get("samples") or 0)
        self.window_s: float = float(payload.get("window_s") or 0.0)
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


class Analyzer:
    """One granted ``logic`` capability."""

    def __init__(self, url: str, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s
        self._url = url

    def capture(self, rate: str, window: str) -> Capture:
        """Take one window of samples and return what every probe did in it."""
        payload = self._post({"command": "capture", "args": {"rate": rate, "window": window}})
        return Capture(payload)

    def configure(self, rows: dict[str, dict[str, str]]) -> dict[str, Any]:
        """Name every probe listed, in one exchange.

        A probe no row names is left as it is, so a bench carrying more than
        this run measures keeps the rest of its labels.
        """
        return self._post({"command": "configure", "args": {"rows": rows}})

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                return dict(json.load(reply))
        except urllib.error.HTTPError as exc:
            raise AnalyzerError(f"{body['command']}: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise AnalyzerError(f"{body['command']}: {exc}") from exc


def _detail(error: urllib.error.HTTPError) -> str:
    """What the instrument said, for an error it explained.

    A rejected command answers 422 carrying the provider's own words, which is
    the difference between "rate must be one of ..." and "HTTP 422".
    """
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"

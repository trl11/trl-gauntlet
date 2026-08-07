"""The acquisition unit, as this suite reaches it.

Gauntlet owns the device. A suite naming ``daq`` in ``requires:`` is granted a
URL and drives it over HTTP, which is why nothing here opens a port or knows
what a DI-2008 is: another eight-channel unit behind the same capability would
need no change.

``urllib`` rather than a client library, because the SDK depends on pydantic
and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class DaqError(RuntimeError):
    """The instrument refused a command, or could not be reached."""


class Daq:
    """One granted ``daq`` capability."""

    def __init__(self, url: str, *, timeout_s: float = 15.0) -> None:
        self._timeout_s = timeout_s
        self._url = url

    def configure(self, rows: dict[str, dict[str, str]]) -> dict[str, Any]:
        """Set the mode and label of every channel named, in one exchange.

        A channel no row names is left as it is, so a bench carrying more than
        this run measures keeps the rest of its settings.
        """
        return self._post({"command": "configure", "args": {"rows": rows}})

    def sample(self) -> dict[str, Any]:
        """Take one scan and return every channel as the instrument reports it.

        A scan rather than a read of the last one: reading would answer from
        whatever the panel last refreshed, which at a slow cadence is a value
        older than the sample it is being recorded as.
        """
        return self._post({"command": "sample", "args": {}})["channels"]

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
            raise DaqError(f"{body['command']}: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise DaqError(f"{body['command']}: {exc}") from exc


def _detail(error: urllib.error.HTTPError) -> str:
    """What the instrument said, for an error it explained.

    A rejected command answers 422 carrying the provider's own words, which is
    the difference between "mode must be one of ..." and "HTTP 422".
    """
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"

"""The camera and its GMSL link, as this suite reaches them.

Gauntlet owns the device. A suite naming ``camera`` in ``requires:`` is granted
a URL and drives it over HTTP, so nothing here opens a node, speaks I2C or
knows which extension unit carries the link telemetry.

``urllib`` rather than a client library, because the SDK depends on pydantic
and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import base64
import binascii
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class LinkError(RuntimeError):
    """The instrument refused a command, or could not be reached."""


@dataclass(frozen=True)
class Snapshot:
    """One still, and what the instrument measured from it."""

    height: int
    image: bytes
    mean_luma: float
    sequence: int
    sharpness: float
    suffix: str
    width: int


@dataclass(frozen=True)
class Reading:
    """Every chip's state at one moment, as the instrument reported it.

    ``chips`` is keyed by I2C address. Counts ending ``_total`` are the run's
    running totals, which the instrument keeps because the chip's own counters
    clear when they are read.
    """

    chips: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str = ""
    identity: dict[str, str] = field(default_factory=dict)

    def chip(self, address: str) -> dict[str, Any]:
        """One chip's figures, or an empty mapping when it did not answer."""
        return self.chips.get(address, {})

    @property
    def locked(self) -> bool:
        """Is every chip reporting its link up."""
        return bool(self.chips) and all(bool(chip.get("locked")) for chip in self.chips.values())


class Camera:
    """One granted ``camera`` capability, with its link telemetry."""

    def __init__(self, url: str, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s
        self._url = url

    def link_status(self) -> Reading:
        """Read every chip now, rather than taking the panel's cached copy."""
        payload = self._post({"command": "link_status", "args": {}})
        chips = payload.get("chips")
        identity = payload.get("identity")
        return Reading(
            chips=dict(chips) if isinstance(chips, dict) else {},
            error=str(payload.get("error") or ""),
            identity=dict(identity) if isinstance(identity, dict) else {},
        )

    def stream_stats(self, *, frames: int) -> dict[str, float]:
        """Read frames back to back and report what the link sustained."""
        payload = self._post({"command": "stream_stats", "args": {"frames": frames}})
        return {key: _number(value) for key, value in payload.items()}

    def snapshot(self, *, max_width: int) -> Snapshot:
        """Take one still and return it with its measurements."""
        payload = self._post({"command": "snapshot", "args": {"max_width": max_width}})
        encoded = payload.get("image_base64")
        if not isinstance(encoded, str) or not encoded:
            raise LinkError("snapshot: the instrument returned no image")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise LinkError(f"snapshot: the image was not valid base64: {exc}") from exc
        if not image:
            raise LinkError("snapshot: the image was empty")
        return Snapshot(
            height=_whole(payload.get("height")),
            image=image,
            mean_luma=_number(payload.get("mean_luma")),
            sequence=_whole(payload.get("sequence")),
            sharpness=_number(payload.get("sharpness")),
            suffix=str(payload.get("suffix") or ".png"),
            width=_whole(payload.get("width")),
        )

    def state(self) -> dict[str, Any]:
        """What the instrument reports about itself, without taking a frame."""
        request = urllib.request.Request(self._url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                return dict(json.load(reply))
        except urllib.error.HTTPError as exc:
            raise LinkError(f"state: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise LinkError(f"state: {exc}") from exc

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
            raise LinkError(f"{body['command']}: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise LinkError(f"{body['command']}: {exc}") from exc


def _detail(error: urllib.error.HTTPError) -> str:
    """What the instrument said, for an error it explained."""
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"


def _number(value: Any) -> float:
    """One measurement, or zero for one the instrument did not report."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _whole(value: Any) -> int:
    """One count, or zero for one the instrument did not report."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
